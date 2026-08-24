from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from linebot.v3.messaging.exceptions import ApiException

from app.api.routes.line import process_text_event, process_text_event_safely
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
async def test_line_loading_animation_uses_bounded_duration(settings) -> None:
    line_bot = LineBotClient(settings)
    line_bot.messaging = AsyncMock()

    await line_bot.show_loading(chat_id="line-chat-id", seconds=90)

    request = line_bot.messaging.show_loading_animation.await_args.args[0]
    assert request.chat_id == "line-chat-id"
    assert request.loading_seconds == 60


@pytest.mark.asyncio
async def test_line_loading_failure_does_not_block_agent_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    user = SimpleNamespace(id="internal-user-id")
    users = AsyncMock()
    users.get_by_line_id.return_value = user
    line_bot = AsyncMock()
    line_bot.show_loading.side_effect = LineUpstreamError(
        "LINE loading animation failed",
        operation="loading",
        status_code=429,
    )
    customer_agent = AsyncMock()
    customer_agent.chat.return_value = "final answer"
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                users=users,
                line_bot=line_bot,
                customer_agent=customer_agent,
            )
        )
    )
    event = SimpleNamespace(
        source=SimpleNamespace(user_id="line-user-id", type="user"),
        reply_token="one-time-reply-token",
        message=SimpleNamespace(text="private customer message"),
    )

    with caplog.at_level(logging.WARNING):
        await process_text_event(request, event, "webhook-event-id")

    assert "line_loading_animation_failed" in caplog.text
    customer_agent.chat.assert_awaited_once()
    line_bot.push_text.assert_awaited_once_with(
        line_user_id="line-user-id",
        text="final answer",
    )


@pytest.mark.asyncio
async def test_line_background_delivery_failure_is_bounded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    users = AsyncMock()
    users.get_by_line_id.return_value = SimpleNamespace(id="internal-user-id")
    line_bot = AsyncMock()
    line_bot.push_text.side_effect = LineUpstreamError(
        "LINE push delivery failed",
        operation="push",
        status_code=401,
    )
    customer_agent = AsyncMock()
    customer_agent.chat.return_value = "final answer"
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                users=users,
                line_bot=line_bot,
                customer_agent=customer_agent,
            )
        )
    )
    event = SimpleNamespace(
        source=SimpleNamespace(user_id="line-user-id", type="user"),
        reply_token="one-time-reply-token",
        message=SimpleNamespace(text="private customer message"),
    )

    with caplog.at_level(logging.ERROR):
        await process_text_event_safely(request, event, "webhook-event-id")

    assert "line_message_delivery_failed" in caplog.text
    assert "private customer message" not in caplog.text
    assert "line-user-id" not in caplog.text
    customer_agent.chat.assert_awaited_once()
