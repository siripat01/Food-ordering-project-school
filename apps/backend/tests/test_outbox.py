from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.domain.common import utc_now
from app.domain.outbox import (
    RETRY_BACKOFF_SECONDS,
    OutboxEvent,
    OutboxEventType,
    OutboxStatus,
    retry_delay_seconds,
)
from app.services.outbox import (
    OutboxLeaseLostError,
    OutboxRepository,
    OutboxService,
)
from tests.fakes import FakeOutboxCollection


def build_service(*, max_attempts: int = 3) -> tuple[OutboxService, FakeOutboxCollection]:
    collection = FakeOutboxCollection()
    db = SimpleNamespace(outbox_events=collection)
    return OutboxService(OutboxRepository(db), max_attempts=max_attempts), collection


@pytest.mark.asyncio
async def test_save_event_persists_a_pending_fact() -> None:
    service, collection = build_service()

    event_id = await service.save_event(
        event_type=OutboxEventType.ORDER_CREATED.value,
        payload={"orderId": "order-1"},
        correlation_id="request-1",
        idempotency_key="order.created:order-1",
    )

    assert event_id is not None
    stored = collection.documents[0]
    assert stored["eventType"] == "order.created"
    assert stored["status"] == OutboxStatus.PENDING.value
    assert stored["attempts"] == 0
    assert stored["payload"] == {"orderId": "order-1"}
    assert stored["correlationId"] == "request-1"
    assert stored["createdAt"].tzinfo is not None


@pytest.mark.asyncio
async def test_save_event_is_idempotent_on_a_repeated_key() -> None:
    service, collection = build_service()
    arguments = {
        "event_type": OutboxEventType.ORDER_CREATED.value,
        "payload": {"orderId": "order-1"},
        "idempotency_key": "order.created:order-1",
    }

    first = await service.save_event(**arguments)
    second = await service.save_event(**arguments)

    assert first is not None
    assert second is None
    assert len(collection.documents) == 1


@pytest.mark.asyncio
async def test_claim_marks_processing_and_assigns_a_fencing_token() -> None:
    service, collection = build_service()
    await service.save_event(
        event_type=OutboxEventType.ORDER_CREATED.value,
        payload={"orderId": "order-1"},
    )

    claimed = await service.claim_pending_events(limit=10)

    assert len(claimed) == 1
    assert claimed[0].attempts == 1
    assert claimed[0].claim_id
    assert collection.documents[0]["status"] == OutboxStatus.PROCESSING.value
    assert collection.documents[0]["claimId"] == claimed[0].claim_id


@pytest.mark.asyncio
async def test_two_dispatchers_cannot_claim_the_same_active_lease() -> None:
    service, _ = build_service()
    other_service, _ = build_service()
    other_service.repository = service.repository
    await service.save_event(
        event_type=OutboxEventType.ORDER_CREATED.value,
        payload={"orderId": "order-1"},
    )

    first = await service.claim_pending_events(limit=10)
    second = await other_service.claim_pending_events(limit=10)

    assert len(first) == 1
    assert second == []


@pytest.mark.asyncio
async def test_mark_as_sent_records_publication_and_releases_the_lease() -> None:
    service, collection = build_service()
    await service.save_event(
        event_type=OutboxEventType.ORDER_CREATED.value,
        payload={"orderId": "order-1"},
    )
    event = (await service.claim_pending_events(limit=1))[0]

    await service.mark_as_sent(event)

    stored = collection.documents[0]
    assert stored["status"] == OutboxStatus.SENT.value
    assert stored["publishedAt"] is not None
    assert stored["lastError"] is None
    assert "claimId" not in stored
    assert "claimedAt" not in stored


@pytest.mark.asyncio
async def test_failed_event_is_rescheduled_with_backoff_and_keeps_its_payload() -> None:
    service, collection = build_service()
    await service.save_event(
        event_type=OutboxEventType.ORDER_CREATED.value,
        payload={"orderId": "order-1"},
    )
    event = (await service.claim_pending_events(limit=1))[0]

    status = await service.mark_as_failed(event, error="BrokerDown: redis unreachable")

    stored = collection.documents[0]
    assert status is OutboxStatus.FAILED
    assert stored["status"] == OutboxStatus.FAILED.value
    assert stored["lastError"].startswith("BrokerDown")
    assert stored["payload"] == {"orderId": "order-1"}
    assert stored["availableAt"] > utc_now()
    assert "claimId" not in stored


@pytest.mark.asyncio
async def test_failed_event_is_claimable_again_once_the_backoff_elapsed() -> None:
    service, collection = build_service()
    await service.save_event(
        event_type=OutboxEventType.ORDER_CREATED.value,
        payload={"orderId": "order-1"},
    )
    event = (await service.claim_pending_events(limit=1))[0]
    await service.mark_as_failed(event, error="BrokerDown")

    assert await service.claim_pending_events(limit=1) == []

    collection.documents[0]["availableAt"] = utc_now() - timedelta(seconds=1)
    retried = await service.claim_pending_events(limit=1)

    assert len(retried) == 1
    assert retried[0].attempts == 2
    assert retried[0].claim_id != event.claim_id


@pytest.mark.asyncio
async def test_event_becomes_dead_after_max_reported_failures() -> None:
    service, collection = build_service(max_attempts=2)
    await service.save_event(
        event_type=OutboxEventType.ORDER_CREATED.value,
        payload={"orderId": "order-1"},
    )

    status = OutboxStatus.PENDING
    for _ in range(2):
        collection.documents[0]["availableAt"] = utc_now() - timedelta(seconds=1)
        event = (await service.claim_pending_events(limit=1))[0]
        status = await service.mark_as_failed(event, error="BrokerDown")

    stored = collection.documents[0]
    assert status is OutboxStatus.DEAD
    assert stored["status"] == OutboxStatus.DEAD.value
    assert stored["payload"] == {"orderId": "order-1"}
    assert stored["lastError"] == "BrokerDown"

    collection.documents[0]["availableAt"] = utc_now() - timedelta(seconds=1)
    assert await service.claim_pending_events(limit=1) == []


@pytest.mark.asyncio
async def test_repeated_dispatcher_crashes_eventually_park_the_event_as_dead() -> None:
    service, collection = build_service(max_attempts=2)
    await service.save_event(
        event_type=OutboxEventType.ORDER_CREATED.value,
        payload={"orderId": "order-1"},
    )

    first = (await service.claim_pending_events(limit=1))[0]
    assert first.attempts == 1
    collection.documents[0]["availableAt"] = utc_now() - timedelta(seconds=1)
    second = (await service.claim_pending_events(limit=1))[0]
    assert second.attempts == 2

    collection.documents[0]["availableAt"] = utc_now() - timedelta(seconds=1)
    assert await service.claim_pending_events(limit=1) == []

    stored = collection.documents[0]
    assert stored["status"] == OutboxStatus.DEAD.value
    assert "maximum attempts" in stored["lastError"]
    assert "claimId" not in stored


@pytest.mark.asyncio
async def test_stale_dispatcher_cannot_overwrite_a_newer_claim() -> None:
    service, collection = build_service()
    await service.save_event(
        event_type=OutboxEventType.ORDER_CREATED.value,
        payload={"orderId": "order-1"},
    )

    stale = (await service.claim_pending_events(limit=1))[0]
    collection.documents[0]["availableAt"] = utc_now() - timedelta(seconds=1)
    current = (await service.claim_pending_events(limit=1))[0]

    assert current.claim_id != stale.claim_id
    await service.mark_as_sent(current)

    with pytest.raises(OutboxLeaseLostError):
        await service.mark_as_failed(stale, error="stale dispatcher")

    stored = collection.documents[0]
    assert stored["status"] == OutboxStatus.SENT.value
    assert stored["lastError"] is None


def test_retry_backoff_is_staged_and_bounded() -> None:
    assert retry_delay_seconds(1) == RETRY_BACKOFF_SECONDS[0]
    assert retry_delay_seconds(2) == RETRY_BACKOFF_SECONDS[1]
    assert retry_delay_seconds(99) == RETRY_BACKOFF_SECONDS[-1]


def test_event_document_round_trips_with_aware_datetimes_and_claim() -> None:
    now = utc_now()
    event = OutboxEvent.from_document(
        {
            "_id": "abc",
            "eventType": "order.created",
            "payload": {"orderId": "order-1"},
            "status": "processing",
            "createdAt": now,
            "availableAt": now,
            "attempts": 1,
            "maxAttempts": 5,
            "correlationId": "request-1",
            "claimId": "claim-1",
        }
    )

    assert event.id == "abc"
    assert event.created_at.tzinfo is not None
    assert event.correlation_id == "request-1"
    assert event.claim_id == "claim-1"
