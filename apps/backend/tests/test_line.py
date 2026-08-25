from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from linebot.v3.messaging.exceptions import ApiException

from app.integrations.line import LineBotClient, LineUpstreamError
from app.services.line_chat import LineChatService


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


def chat_service(settings, *, users, line_bot, agent, push, reply) -> LineChatService:
    return LineChatService(
        settings=settings,
        users=users,
        line_bot=line_bot,
        agent=agent,
        push_messages=push,
        reply_messages=reply,
    )


@pytest.mark.asyncio
async def test_line_loading_failure_does_not_block_agent_response(
    settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    users = AsyncMock()
    users.get_by_line_id.return_value = SimpleNamespace(id="internal-user-id")
    line_bot = AsyncMock()
    line_bot.show_loading.side_effect = LineUpstreamError(
        "LINE loading animation failed",
        operation="loading",
        status_code=429,
    )
    agent = AsyncMock()
    agent.chat.return_value = "final answer"
    push = AsyncMock()
    service = chat_service(
        settings, users=users, line_bot=line_bot, agent=agent, push=push, reply=AsyncMock()
    )

    with caplog.at_level(logging.WARNING):
        await service.handle_text_message(
            line_user_id="line-user-id",
            text="private customer message",
            reply_token="one-time-reply-token",
            idempotency_key="line:webhook-event-id",
        )

    assert "line_loading_animation_failed" in caplog.text
    agent.chat.assert_awaited_once()
    push.assert_awaited_once_with(
        "line-user-id",
        [{"type": "text", "text": "final answer"}],
        None,
    )


@pytest.mark.asyncio
async def test_line_delivery_is_queued_rather_than_sent_inline(settings) -> None:
    """A LINE outage must retry the message alone, not re-run the whole LLM turn."""
    users = AsyncMock()
    users.get_by_line_id.return_value = SimpleNamespace(id="internal-user-id")
    line_bot = AsyncMock()
    agent = AsyncMock()
    agent.chat.return_value = "final answer"
    push = AsyncMock()
    service = chat_service(
        settings, users=users, line_bot=line_bot, agent=agent, push=push, reply=AsyncMock()
    )

    await service.handle_text_message(
        line_user_id="line-user-id",
        text="private customer message",
        reply_token="one-time-reply-token",
        idempotency_key="line:webhook-event-id",
    )

    line_bot.push_text.assert_not_awaited()
    line_bot.push_messages.assert_not_awaited()
    push.assert_awaited_once()


@pytest.mark.asyncio
async def test_unregistered_line_user_is_replied_to_without_reaching_the_agent(
    settings,
) -> None:
    users = AsyncMock()
    users.get_by_line_id.return_value = None
    agent = AsyncMock()
    reply = AsyncMock()
    push = AsyncMock()
    service = chat_service(
        settings,
        users=users,
        line_bot=AsyncMock(),
        agent=agent,
        push=push,
        reply=reply,
    )

    await service.handle_text_message(
        line_user_id="line-user-id",
        text="private customer message",
        reply_token="one-time-reply-token",
        idempotency_key="line:webhook-event-id",
    )

    agent.chat.assert_not_awaited()
    push.assert_not_awaited()
    reply.assert_awaited_once()
    assert "เข้าสู่ระบบ" in reply.await_args.args[1][0]["text"]
