from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId

from app.domain.orders import OrderResponse, OrderStatus, StatusHistoryEntry
from app.services.order_updates import (
    LineOrderStatusNotifier,
    OrderEventBroker,
    OrderUpdateDispatcher,
)


def order(status: OrderStatus = OrderStatus.PENDING) -> OrderResponse:
    now = datetime.now(UTC)
    return OrderResponse(
        id=str(ObjectId()),
        user_id=str(ObjectId()),
        items=[],
        subtotal=Decimal("0"),
        total=Decimal("0"),
        status=status,
        status_history=[StatusHistoryEntry(status=status, changed_at=now)],
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_order_event_broker_fans_out_latest_bounded_event() -> None:
    broker = OrderEventBroker(queue_size=1)
    first = order()
    latest = order(OrderStatus.CONFIRMED)

    async with broker.subscribe() as queue:
        broker.publish(first)
        broker.publish(latest)
        event = queue.get_nowait()

    assert event.name == "order.updated"
    assert event.order.id == latest.id


@pytest.mark.asyncio
async def test_line_notifier_uses_internal_line_identity_only_for_status_updates() -> None:
    users = AsyncMock()
    users.get_line_user_id.return_value = "line-recipient"
    notifier = LineOrderStatusNotifier(
        settings=SimpleNamespace(line_enabled=True),
        users=users,
    )

    assert notifier.build_status_messages(order(OrderStatus.PENDING)) == []

    confirmed = order(OrderStatus.CONFIRMED)
    messages = notifier.build_status_messages(confirmed)
    recipient = await notifier.resolve_recipient(confirmed)

    users.get_line_user_id.assert_awaited_once_with(confirmed.user_id)
    assert recipient == "line-recipient"
    assert messages[0]["type"] == "text"
    assert notifier.STATUS_MESSAGES[OrderStatus.CONFIRMED] in messages[0]["text"]


@pytest.mark.asyncio
async def test_line_notifier_returns_no_recipient_when_line_is_disabled() -> None:
    users = AsyncMock()
    notifier = LineOrderStatusNotifier(
        settings=SimpleNamespace(line_enabled=False),
        users=users,
    )

    assert await notifier.resolve_recipient(order(OrderStatus.CONFIRMED)) is None
    users.get_line_user_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatcher_publishes_committed_update_to_staff_subscribers() -> None:
    broker = OrderEventBroker(queue_size=10)
    dispatcher = OrderUpdateDispatcher(broker=broker)
    completed = order(OrderStatus.COMPLETED)

    async with broker.subscribe() as queue:
        dispatcher.publish(completed)
        event = queue.get_nowait()

    assert event.name == "order.updated"
    assert event.order.id == completed.id
