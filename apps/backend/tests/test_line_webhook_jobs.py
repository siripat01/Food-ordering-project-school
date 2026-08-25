from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.api.routes import line as line_route


class FakeTextMessage:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeMessageEvent:
    def __init__(self, event_id: str = "event-1") -> None:
        self.webhook_event_id = event_id
        self.reply_token = event_id
        self.source = SimpleNamespace(user_id="line-user")
        self.message = FakeTextMessage("hello")


def request_for(event: FakeMessageEvent) -> SimpleNamespace:
    line_bot = SimpleNamespace(parse=Mock(return_value=[event]))
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(line_enabled=True),
                line_bot=line_bot,
            )
        ),
        state=SimpleNamespace(request_id="request-1"),
        headers={"X-Line-Signature": "signature"},
        body=AsyncMock(return_value=b"{}"),
    )


@pytest.mark.asyncio
async def test_line_webhook_enqueues_with_a_stable_idempotency_key(monkeypatch) -> None:
    monkeypatch.setattr(line_route, "MessageEvent", FakeMessageEvent)
    monkeypatch.setattr(line_route, "TextMessageContent", FakeTextMessage)
    enqueue = AsyncMock()
    monkeypatch.setattr(line_route.process_agent_message, "kiq", enqueue)
    request = request_for(FakeMessageEvent("event-42"))

    assert await line_route.line_webhook(request) == {"status": "accepted"}

    enqueue.assert_awaited_once_with(
        line_user_id="line-user",
        message="hello",
        reply_token="event-42",
        idempotency_key="line:event-42",
        correlation_id="request-1",
    )


@pytest.mark.asyncio
async def test_line_webhook_retry_can_enqueue_again_without_losing_the_event(
    monkeypatch,
) -> None:
    """Duplicate enqueue is preferable to a claim-before-enqueue loss window."""

    monkeypatch.setattr(line_route, "MessageEvent", FakeMessageEvent)
    monkeypatch.setattr(line_route, "TextMessageContent", FakeTextMessage)
    enqueue = AsyncMock()
    monkeypatch.setattr(line_route.process_agent_message, "kiq", enqueue)
    request = request_for(FakeMessageEvent("event-42"))

    await line_route.line_webhook(request)
    await line_route.line_webhook(request)

    assert enqueue.await_count == 2
    assert {call.kwargs["idempotency_key"] for call in enqueue.await_args_list} == {
        "line:event-42"
    }


@pytest.mark.asyncio
async def test_line_webhook_returns_retryable_error_when_enqueue_fails(monkeypatch) -> None:
    monkeypatch.setattr(line_route, "MessageEvent", FakeMessageEvent)
    monkeypatch.setattr(line_route, "TextMessageContent", FakeTextMessage)
    enqueue = AsyncMock(side_effect=ConnectionError("redis down"))
    monkeypatch.setattr(line_route.process_agent_message, "kiq", enqueue)
    request = request_for(FakeMessageEvent())

    with pytest.raises(HTTPException) as captured:
        await line_route.line_webhook(request)

    assert captured.value.status_code == 503
