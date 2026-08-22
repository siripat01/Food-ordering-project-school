from __future__ import annotations

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
