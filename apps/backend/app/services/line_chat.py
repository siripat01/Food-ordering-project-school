from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from app.core.config import Settings
from app.core.security import TokenType, create_token
from app.integrations.agent.service import CustomerAgentService
from app.integrations.line import LineBotClient, LineUpstreamError
from app.services.users import UserService

logger = logging.getLogger(__name__)

CHAT_TICKET_LIFETIME = timedelta(minutes=10)
LOADING_ANIMATION_SECONDS = 60

#: Injected so this service never imports a task module or the Redis broker.
PushMessages = Callable[[str, list[dict[str, Any]], str | None], Awaitable[None]]
ReplyMessages = Callable[[str, list[dict[str, Any]], str | None], Awaitable[None]]


class LineChatService:
    """Turns one inbound LINE text message into an assistant answer.

    This is the business flow behind the ``agent.process`` task. Outbound
    delivery is handed to ``line.push`` / ``line.reply`` so that a LINE outage
    retries the message alone instead of re-running the whole LLM turn.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        users: UserService,
        line_bot: LineBotClient,
        agent: CustomerAgentService,
        push_messages: PushMessages,
        reply_messages: ReplyMessages,
    ) -> None:
        self.settings = settings
        self.users = users
        self.line_bot = line_bot
        self.agent = agent
        self.push_messages = push_messages
        self.reply_messages = reply_messages

    def _login_url(self, line_user_id: str) -> str:
        ticket = create_token(
            subject=line_user_id,
            token_type=TokenType.LINE_CHAT,
            settings=self.settings,
            lifetime=CHAT_TICKET_LIFETIME,
        )
        return (
            f"{str(self.settings.backend_url).rstrip('/')}"
            f"/api/v1/auth/line?origin=chat&chat_ticket={ticket}"
        )

    async def handle_text_message(
        self,
        *,
        line_user_id: str,
        text: str,
        reply_token: str | None,
        idempotency_key: str,
        correlation_id: str | None = None,
        show_loading: bool = True,
    ) -> None:
        user = await self.users.get_by_line_id(line_user_id)
        if user is None:
            if reply_token:
                await self.reply_messages(
                    reply_token,
                    [
                        {
                            "type": "text",
                            "text": (f"กรุณาเข้าสู่ระบบก่อนใช้งาน\n{self._login_url(line_user_id)}"),
                        }
                    ],
                    correlation_id,
                )
            return
        if show_loading:
            try:
                await self.line_bot.show_loading(
                    chat_id=line_user_id,
                    seconds=LOADING_ANIMATION_SECONDS,
                )
            except LineUpstreamError as exc:
                # A missing typing indicator must never block the answer.
                logger.warning(
                    "line_loading_animation_failed",
                    extra={
                        "correlation_id": correlation_id,
                        "error_type": type(exc).__name__,
                        "line_operation": exc.operation,
                        "upstream_status": exc.status_code,
                    },
                )
        answer = await self.agent.chat(
            identity=user,
            message=text,
            idempotency_key=idempotency_key,
        )
        await self.push_messages(
            line_user_id,
            [{"type": "text", "text": answer}],
            correlation_id,
        )
