from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymongo.errors import DuplicateKeyError

from app.services.idempotency import IdempotencyService
from app.services.oauth import OAuthStateService
from app.services.webhooks import WebhookEventService


@pytest.mark.asyncio
async def test_oauth_state_is_consumed_atomically_and_only_once(settings) -> None:
    states = AsyncMock()
    states.find_one_and_delete.side_effect = [
        {"nonce": "nonce", "origin": "web"},
        None,
    ]
    service = OAuthStateService(SimpleNamespace(oauth_states=states), settings)

    first = await service.consume("opaque-state")
    second = await service.consume("opaque-state")

    assert first is not None
    assert second is None
    query = states.find_one_and_delete.await_args_list[0].args[0]
    assert query["stateHash"] != "opaque-state"
    assert "expiresAt" in query


@pytest.mark.asyncio
async def test_duplicate_line_webhook_event_is_not_claimed_twice() -> None:
    events = AsyncMock()
    events.insert_one.side_effect = [SimpleNamespace(), DuplicateKeyError("duplicate")]
    service = WebhookEventService(SimpleNamespace(webhook_events=events))

    assert await service.claim("event-1") is True
    assert await service.claim("event-1") is False


@pytest.mark.asyncio
async def test_job_completion_marker_suppresses_completed_duplicate() -> None:
    keys = AsyncMock()
    keys.find_one.side_effect = [None, {"_id": "completed"}]
    service = IdempotencyService(SimpleNamespace(job_idempotency=keys))

    assert await service.is_completed(scope="agent.process", key="line:event-1") is False
    assert await service.is_completed(scope="agent.process", key="line:event-1") is True

    keys.find_one.assert_awaited_with(
        {"scope": "agent.process", "key": "line:event-1"},
        projection={"_id": 1},
    )


@pytest.mark.asyncio
async def test_duplicate_completion_marker_is_harmless() -> None:
    keys = AsyncMock()
    keys.insert_one.side_effect = DuplicateKeyError("duplicate")
    service = IdempotencyService(SimpleNamespace(job_idempotency=keys))

    await service.mark_completed(scope="agent.process", key="line:event-1")

    keys.insert_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_job_completion_marker_expires() -> None:
    keys = AsyncMock()
    service = IdempotencyService(SimpleNamespace(job_idempotency=keys), retention_hours=2)

    await service.mark_completed(scope="agent.process", key="line:event-1")

    document = keys.insert_one.await_args.args[0]
    assert document["expiresAt"] > document["completedAt"]
    assert document["completedAt"].tzinfo is not None
