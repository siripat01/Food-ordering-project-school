from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.jobs import TaskName
from app.domain.outbox import OutboxEventType, OutboxStatus
from app.jobs.dispatcher import (
    EVENT_HANDLERS,
    EVENT_TASK_NAMES,
    OutboxDispatcher,
    OutboxPollingDispatcher,
)
from app.services.outbox import OutboxRepository, OutboxService
from tests.fakes import FakeOutboxCollection


def build_service() -> tuple[OutboxService, FakeOutboxCollection]:
    collection = FakeOutboxCollection()
    return OutboxService(
        OutboxRepository(SimpleNamespace(outbox_events=collection))
    ), collection


async def seed(service: OutboxService, event_type: str) -> None:
    await service.save_event(
        event_type=event_type,
        payload={"orderId": "order-1", "userId": "user-1"},
        correlation_id="request-1",
    )


def test_every_supported_event_type_has_exactly_one_route() -> None:
    assert set(EVENT_HANDLERS) == {event.value for event in OutboxEventType}
    assert EVENT_TASK_NAMES[OutboxEventType.ORDER_CREATED.value] is TaskName.ORDER_PROCESS
    assert (
        EVENT_TASK_NAMES[OutboxEventType.ORDER_STATUS_CHANGED.value]
        is TaskName.ORDER_UPDATE_STATUS
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type", "task_name"),
    [
        (OutboxEventType.ORDER_CREATED.value, TaskName.ORDER_PROCESS),
        (OutboxEventType.ORDER_STATUS_CHANGED.value, TaskName.ORDER_UPDATE_STATUS),
    ],
)
async def test_event_is_routed_to_its_task_and_marked_sent(
    event_type: str, task_name: TaskName
) -> None:
    service, collection = build_service()
    await seed(service, event_type)
    enqueued: list[tuple[str, dict]] = []

    async def handler(event, name=task_name):
        enqueued.append((name.value, event.payload))

    dispatcher = OutboxDispatcher(service, handlers={event_type: handler})

    assert await dispatcher.run_once() == 1

    assert enqueued == [(task_name.value, {"orderId": "order-1", "userId": "user-1"})]
    assert collection.documents[0]["status"] == OutboxStatus.SENT.value


@pytest.mark.asyncio
async def test_failed_enqueue_leaves_the_event_retryable_and_never_sent() -> None:
    service, collection = build_service()
    await seed(service, OutboxEventType.ORDER_CREATED.value)

    async def broken_handler(event):
        raise ConnectionError("redis unreachable")

    dispatcher = OutboxDispatcher(
        service, handlers={OutboxEventType.ORDER_CREATED.value: broken_handler}
    )

    await dispatcher.run_once()

    stored = collection.documents[0]
    assert stored["status"] == OutboxStatus.FAILED.value
    assert "redis unreachable" in stored["lastError"]
    assert stored["publishedAt"] is None


@pytest.mark.asyncio
async def test_unknown_event_type_is_parked_as_dead_without_crashing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, collection = build_service()
    await seed(service, "order.teleported")
    dispatcher = OutboxDispatcher(service)

    with caplog.at_level(logging.ERROR):
        handled = await dispatcher.run_once()

    assert handled == 1
    assert collection.documents[0]["status"] == OutboxStatus.DEAD.value
    assert "No task route" in collection.documents[0]["lastError"]


@pytest.mark.asyncio
async def test_one_failing_event_does_not_stop_the_batch() -> None:
    service, collection = build_service()
    await service.save_event(
        event_type=OutboxEventType.ORDER_CREATED.value,
        payload={"orderId": "broken"},
        idempotency_key="a",
    )
    await service.save_event(
        event_type=OutboxEventType.ORDER_CREATED.value,
        payload={"orderId": "healthy"},
        idempotency_key="b",
    )

    async def selective(event):
        if event.payload["orderId"] == "broken":
            raise ConnectionError("redis unreachable")

    dispatcher = OutboxDispatcher(
        service, handlers={OutboxEventType.ORDER_CREATED.value: selective}
    )

    assert await dispatcher.run_once() == 2

    statuses = {d["payload"]["orderId"]: d["status"] for d in collection.documents}
    assert statuses == {
        "broken": OutboxStatus.FAILED.value,
        "healthy": OutboxStatus.SENT.value,
    }


@pytest.mark.asyncio
async def test_polling_loop_survives_a_failing_claim(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dispatcher = AsyncMock()
    polling = OutboxPollingDispatcher(
        dispatcher,
        poll_interval_seconds=0.01,
        idle_interval_seconds=0.01,
    )
    calls = {"count": 0}

    async def run_once() -> int:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("mongo unreachable")
        polling.stop()
        return 0

    dispatcher.run_once = run_once

    with caplog.at_level(logging.ERROR):
        await polling.run()

    assert calls["count"] == 2
    assert "outbox_poll_failed" in caplog.text


@pytest.mark.asyncio
async def test_real_routes_enqueue_the_declared_task(monkeypatch) -> None:
    """The production registry must call the real task objects, not a stub."""
    from app.jobs import order as order_tasks

    process = AsyncMock()
    monkeypatch.setattr(order_tasks.process_order, "kiq", process)
    service, _ = build_service()
    await seed(service, OutboxEventType.ORDER_CREATED.value)

    await OutboxDispatcher(service).run_once()

    process.assert_awaited_once_with(order_id="order-1", correlation_id="request-1")
