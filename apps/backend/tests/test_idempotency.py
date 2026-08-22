from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymongo.errors import DuplicateKeyError

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
