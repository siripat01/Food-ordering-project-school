from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from pydantic import SecretStr

from app.domain.orders import OrderResponse, OrderStatus, StatusHistoryEntry
from app.domain.recommendations import RecommendationEventCreate
from app.services.order_updates import (
    LineOrderStatusNotifier,
    OrderEventBroker,
    OrderUpdateDispatcher,
)
from app.services.recommendations import RecommendationService
from scripts.evaluate_recommendations import evaluate_leave_one_out, ndcg_at_k, recall_at_k


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
async def test_dispatcher_records_completed_purchase_after_committed_update() -> None:
    broker = OrderEventBroker(queue_size=10)
    recommendations = AsyncMock()
    dispatcher = OrderUpdateDispatcher(
        broker=broker,
        recommendations=recommendations,
    )
    completed = order(OrderStatus.COMPLETED)

    dispatcher.publish(completed)
    await dispatcher.close()

    recommendations.record_purchase.assert_awaited_once_with(completed)


@pytest.mark.asyncio
async def test_recommendation_event_is_idempotent_and_pseudonymous() -> None:
    product_id = str(ObjectId())
    events = AsyncMock()
    events.find_one.return_value = None
    slates = AsyncMock()
    counters = AsyncMock()
    counters.find_one_and_update.return_value = {"count": 1}
    products = AsyncMock()
    products.get_available_by_ids.return_value = [SimpleNamespace(id=product_id)]
    settings = SimpleNamespace(
        recommendation_user_ref_secret=SecretStr("recommendation-test-secret-0123456789abcdef"),
        recommendation_user_ref_key_version="v1",
        recommender_enabled=False,
        recommendation_daily_impression_cap=10,
        recommendation_daily_click_cap=5,
        recommendation_daily_add_to_cart_cap=3,
    )
    user_id = str(ObjectId())
    service_db = SimpleNamespace(
        recommendation_events=events,
        recommendation_slates=slates,
        recommendation_event_counters=counters,
    )
    service = RecommendationService(
        db=service_db,
        products=products,
        settings=settings,
        http_client=AsyncMock(),
    )
    payload = RecommendationEventCreate(
        event_type="click",
        product_id=product_id,
        recommendation_id="recommendation-123",
    )
    slates.find_one.return_value = {
        "_id": payload.recommendation_id,
        "items": [{"productId": product_id, "rank": 2}],
        "placement": "customer-home",
        "strategy": "recent",
        "modelVersion": "baseline-v1",
    }

    created = await service.record_client_event(user_id=user_id, payload=payload)
    inserted = events.insert_one.await_args.args[0]

    assert created is True
    assert "userId" not in inserted
    assert "eventId" not in inserted
    assert "weight" not in inserted
    assert len(inserted["userRef"]) == 64
    assert inserted["rank"] == 2
    events.find_one.return_value = {"dedupeKey": inserted["dedupeKey"]}
    duplicate = await service.record_client_event(
        user_id=user_id,
        payload=payload,
    )
    assert duplicate is False


def test_popularity_offline_metrics_are_computed_without_user_identifiers() -> None:
    now = datetime.now(UTC)
    events = [
        {"userRef": "u1", "productId": "a", "createdAt": now - timedelta(days=3)},
        {"userRef": "u1", "productId": "b", "createdAt": now - timedelta(days=1)},
        {"userRef": "u2", "productId": "b", "createdAt": now - timedelta(days=2)},
        {"userRef": "u2", "productId": "a", "createdAt": now},
    ]

    result = evaluate_leave_one_out(events, k=2)

    assert result["users"] == 2
    assert result["recallAtK"] == 1.0
    assert 0 < result["ndcgAtK"] <= 1
    assert recall_at_k(["a"], {"a"}, 1) == 1.0
    assert ndcg_at_k(["a"], {"a"}, 1) == 1.0
