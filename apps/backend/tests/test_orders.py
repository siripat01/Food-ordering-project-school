from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from bson import ObjectId
from bson.decimal128 import Decimal128

from app.domain.errors import ConflictError, NotFoundError
from app.domain.orders import OrderCreate, OrderStatus
from app.domain.users import Role
from app.services.orders import OrderService


class Cursor:
    def __init__(self, documents):
        self.documents = documents

    async def to_list(self):
        return self.documents


@pytest.mark.asyncio
async def test_order_price_is_calculated_from_product_catalog() -> None:
    product_id = ObjectId()
    products = SimpleNamespace()
    products.find = Mock(
        return_value=Cursor(
            [
                {
                    "_id": product_id,
                    "productName": "Trusted product",
                    "price": Decimal128("50.00"),
                    "status": "available",
                    "addons": [
                        {
                            "id": "egg",
                            "name": "Fried egg",
                            "price": Decimal128("10.00"),
                            "available": True,
                        }
                    ],
                }
            ]
        )
    )
    orders = AsyncMock()
    orders.find_one.return_value = None
    orders.insert_one.return_value = SimpleNamespace(inserted_id=ObjectId())
    db = SimpleNamespace(products=products, orders=orders)
    service = OrderService(db)

    result, created = await service.create(
        user_id=str(ObjectId()),
        idempotency_key="request-12345678",
        payload=OrderCreate.model_validate(
            {
                "items": [
                    {
                        "product_id": str(product_id),
                        "quantity": 2,
                        "addon_ids": ["egg"],
                        "note": "less spicy",
                    }
                ]
            }
        ),
    )

    assert created is True
    assert result.total == Decimal("120.00")
    inserted = orders.insert_one.await_args.args[0]
    assert inserted["total"].to_decimal() == Decimal("120.00")
    assert "price" not in inserted["items"][0]
    assert inserted["items"][0]["unitPrice"].to_decimal() == Decimal("50.00")


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_returns_existing_order() -> None:
    order_id = ObjectId()
    user_id = str(ObjectId())
    now = datetime.now(UTC)
    existing = {
        "_id": order_id,
        "userId": user_id,
        "items": [],
        "subtotal": Decimal128("0.00"),
        "total": Decimal128("0.00"),
        "status": "pending",
        "statusHistory": [{"status": "pending", "changedAt": now}],
        "createdAt": now,
        "updatedAt": now,
        "schemaVersion": 2,
    }
    orders = AsyncMock()
    orders.find_one.return_value = existing
    db = SimpleNamespace(products=AsyncMock(), orders=orders)

    result, created = await OrderService(db).create(
        user_id=user_id,
        idempotency_key="same-request-123",
        payload=OrderCreate.model_validate(
            {"items": [{"product_id": str(ObjectId()), "quantity": 1}]}
        ),
    )

    assert created is False
    assert result.id == str(order_id)
    orders.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_order_cannot_transition_back_to_pending() -> None:
    order_id = ObjectId()
    orders = AsyncMock()
    orders.find_one.return_value = {"_id": order_id, "status": "completed"}
    db = SimpleNamespace(orders=orders)

    with pytest.raises(ConflictError):
        await OrderService(db).transition(
            order_id=str(order_id),
            new_status=OrderStatus.PENDING,
            actor_id=str(ObjectId()),
            actor_role=Role.STAFF,
        )

    orders.find_one_and_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_customer_cannot_cancel_another_customers_order() -> None:
    orders = AsyncMock()
    orders.find_one_and_update.return_value = None
    orders.find_one.return_value = None
    db = SimpleNamespace(orders=orders)

    with pytest.raises(NotFoundError):
        await OrderService(db).cancel_own(
            order_id=str(ObjectId()),
            user_id=str(ObjectId()),
        )


class FakeTransaction:
    """Models MongoDB's session semantics: one session, all-or-nothing."""

    def __init__(self, *, supported: bool = True) -> None:
        self.supported = supported
        self.session = SimpleNamespace(name="session") if supported else None
        self.entered = 0

    @asynccontextmanager
    async def __call__(self):
        self.entered += 1
        yield self.session


def order_db(*, products=None, orders=None, transaction=None):
    db = SimpleNamespace(
        products=products or SimpleNamespace(find=Mock(return_value=Cursor([]))),
        orders=orders or AsyncMock(),
    )
    db.transaction = transaction or FakeTransaction()
    return db


def catalog(product_id):
    products = SimpleNamespace()
    products.find = Mock(
        return_value=Cursor(
            [
                {
                    "_id": product_id,
                    "productName": "Trusted product",
                    "price": Decimal128("50.00"),
                    "status": "available",
                    "addons": [],
                }
            ]
        )
    )
    return products


@pytest.mark.asyncio
async def test_order_and_outbox_event_are_written_in_one_transaction() -> None:
    product_id = ObjectId()
    orders = AsyncMock()
    orders.find_one.return_value = None
    transaction = FakeTransaction()
    db = order_db(products=catalog(product_id), orders=orders, transaction=transaction)
    outbox = AsyncMock()
    service = OrderService(db, outbox=outbox)

    result, created = await service.create(
        user_id=str(ObjectId()),
        idempotency_key="request-12345678",
        payload=OrderCreate.model_validate(
            {"items": [{"product_id": str(product_id), "quantity": 1}]}
        ),
    )

    assert created is True
    assert transaction.entered == 1
    insert_session = orders.insert_one.await_args.kwargs["session"]
    event = outbox.save_event.await_args.kwargs
    assert insert_session is transaction.session
    assert event["session"] is transaction.session
    assert event["event_type"] == "order.created"
    assert event["payload"] == {
        "orderId": result.id,
        "userId": result.user_id,
        "status": "pending",
    }
    assert event["idempotency_key"] == f"order.created:{result.id}"


@pytest.mark.asyncio
async def test_failed_outbox_write_aborts_the_order_creation() -> None:
    product_id = ObjectId()
    orders = AsyncMock()
    orders.find_one.return_value = None
    db = order_db(products=catalog(product_id), orders=orders)
    outbox = AsyncMock()
    outbox.save_event.side_effect = RuntimeError("outbox unavailable")

    with pytest.raises(RuntimeError):
        await OrderService(db, outbox=outbox).create(
            user_id=str(ObjectId()),
            idempotency_key="request-12345678",
            payload=OrderCreate.model_validate(
                {"items": [{"product_id": str(product_id), "quantity": 1}]}
            ),
        )


@pytest.mark.asyncio
async def test_status_transition_writes_a_status_changed_event_in_the_transaction() -> None:
    order_id = ObjectId()
    now = datetime.now(UTC)
    orders = AsyncMock()
    orders.find_one.return_value = {"_id": order_id, "status": "pending"}
    orders.find_one_and_update.return_value = {
        "_id": order_id,
        "userId": str(ObjectId()),
        "items": [],
        "subtotal": Decimal128("0.00"),
        "total": Decimal128("0.00"),
        "status": "confirmed",
        "statusHistory": [{"status": "confirmed", "changedAt": now}],
        "createdAt": now,
        "updatedAt": now,
        "schemaVersion": 2,
    }
    transaction = FakeTransaction()
    db = order_db(orders=orders, transaction=transaction)
    outbox = AsyncMock()

    order = await OrderService(db, outbox=outbox).transition(
        order_id=str(order_id),
        new_status=OrderStatus.CONFIRMED,
        actor_id=str(ObjectId()),
        actor_role=Role.STAFF,
    )

    assert transaction.entered == 1
    event = outbox.save_event.await_args.kwargs
    assert event["session"] is transaction.session
    assert event["event_type"] == "order.status_changed"
    assert event["payload"]["status"] == "confirmed"
    assert event["idempotency_key"] == f"order.status_changed:{order.id}:confirmed"


@pytest.mark.asyncio
async def test_customer_cancellation_writes_a_status_changed_event() -> None:
    order_id = ObjectId()
    user_id = str(ObjectId())
    now = datetime.now(UTC)
    orders = AsyncMock()
    orders.find_one_and_update.return_value = {
        "_id": order_id,
        "userId": user_id,
        "items": [],
        "subtotal": Decimal128("0.00"),
        "total": Decimal128("0.00"),
        "status": "cancelled",
        "statusHistory": [{"status": "cancelled", "changedAt": now}],
        "createdAt": now,
        "updatedAt": now,
        "schemaVersion": 2,
    }
    db = order_db(orders=orders)
    outbox = AsyncMock()

    await OrderService(db, outbox=outbox).cancel_own(order_id=str(order_id), user_id=user_id)

    event = outbox.save_event.await_args.kwargs
    assert event["event_type"] == "order.status_changed"
    assert event["payload"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_no_outbox_event_is_written_when_the_cancellation_is_rejected() -> None:
    order_id = ObjectId()
    user_id = str(ObjectId())
    orders = AsyncMock()
    orders.find_one_and_update.return_value = None
    orders.find_one.return_value = {"_id": order_id, "userId": user_id, "status": "completed"}
    db = order_db(orders=orders)
    outbox = AsyncMock()

    with pytest.raises(ConflictError):
        await OrderService(db, outbox=outbox).cancel_own(
            order_id=str(order_id), user_id=user_id
        )

    outbox.save_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_standalone_mongodb_still_writes_both_documents_without_a_session() -> None:
    """Documented fallback: no replica set means no atomicity, not no outbox."""
    product_id = ObjectId()
    orders = AsyncMock()
    orders.find_one.return_value = None
    transaction = FakeTransaction(supported=False)
    db = order_db(products=catalog(product_id), orders=orders, transaction=transaction)
    outbox = AsyncMock()

    await OrderService(db, outbox=outbox).create(
        user_id=str(ObjectId()),
        idempotency_key="request-12345678",
        payload=OrderCreate.model_validate(
            {"items": [{"product_id": str(product_id), "quantity": 1}]}
        ),
    )

    assert orders.insert_one.await_args.kwargs["session"] is None
    assert outbox.save_event.await_args.kwargs["session"] is None
