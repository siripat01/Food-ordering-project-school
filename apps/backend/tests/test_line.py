from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from linebot.v3.messaging.exceptions import ApiException

from app.api.routes.line import process_text_event_safely
from app.integrations.line import LineBotClient, LineUpstreamError


@pytest.mark.asyncio
async def test_line_sdk_error_is_wrapped_without_upstream_body(settings) -> None:
    line_bot = LineBotClient(settings)
    line_bot.messaging = AsyncMock()
    line_bot.messaging.reply_message.side_effect = ApiException(
        status=401,
        reason="provider response must not be exposed",
    )

    with pytest.raises(LineUpstreamError) as captured:
        await line_bot.reply_text(reply_token="one-time-reply-token", text="hello")

    assert captured.value.operation == "reply"
    assert captured.value.status_code == 401
    assert str(captured.value) == "LINE reply delivery failed"
    assert "provider response" not in str(captured.value)


@pytest.mark.asyncio
async def test_line_background_delivery_failure_is_bounded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    users = AsyncMock()
    users.get_by_line_id.return_value = SimpleNamespace(id="internal-user-id")
    line_bot = AsyncMock()
    line_bot.reply_text.side_effect = LineUpstreamError(
        "LINE reply delivery failed",
        operation="reply",
        status_code=401,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                users=users,
                line_bot=line_bot,
                customer_agent=AsyncMock(),
            )
        )
    )
    event = SimpleNamespace(
        source=SimpleNamespace(user_id="line-user-id"),
        reply_token="one-time-reply-token",
        message=SimpleNamespace(text="private customer message"),
    )

    with caplog.at_level(logging.ERROR):
        await process_text_event_safely(request, event, "webhook-event-id")

    assert "line_message_delivery_failed" in caplog.text
    assert "private customer message" not in caplog.text
    assert "line-user-id" not in caplog.text
    request.app.state.customer_agent.chat.assert_not_awaited()
