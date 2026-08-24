from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from app.core.security import TokenType, create_token
from app.integrations.line import LineUpstreamError

router = APIRouter(prefix="/line", tags=["line"])
logger = logging.getLogger(__name__)


async def process_text_event(request: Request, event: Any, event_id: str) -> None:
    line_user_id = getattr(event.source, "user_id", None)
    if not line_user_id:
        return
    user = await request.app.state.users.get_by_line_id(line_user_id)
    if user is None:
        ticket = create_token(
            subject=line_user_id,
            token_type=TokenType.LINE_CHAT,
            settings=request.app.state.settings,
            lifetime=timedelta(minutes=10),
        )
        login_url = (
            f"{str(request.app.state.settings.backend_url).rstrip('/')}"
            f"/api/v1/auth/line?origin=chat&chat_ticket={ticket}"
        )
        await request.app.state.line_bot.reply_text(
            reply_token=event.reply_token,
            text=f"กรุณาเข้าสู่ระบบก่อนใช้งาน\n{login_url}",
        )
        return
    await request.app.state.line_bot.reply_text(
        reply_token=event.reply_token, text="กำลังประมวลผลคำขอครับ"
    )
    answer = await request.app.state.customer_agent.chat(
        identity=user,
        message=event.message.text,
        idempotency_key=f"line:{event_id}",
    )
    await request.app.state.line_bot.push_text(line_user_id=line_user_id, text=answer)


async def process_text_event_safely(request: Request, event: Any, event_id: str) -> None:
    """Keep post-response LINE failures bounded and free of upstream response bodies."""
    try:
        await process_text_event(request, event, event_id)
    except LineUpstreamError as exc:
        logger.error(
            "line_message_delivery_failed",
            extra={
                "error_type": type(exc).__name__,
                "line_operation": exc.operation,
                "upstream_status": exc.status_code,
            },
        )
    except Exception as exc:
        logger.error(
            "line_text_event_processing_failed",
            extra={"error_type": type(exc).__name__},
        )


@router.post("/webhook")
async def line_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    if not request.app.state.settings.line_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LINE integration is disabled",
        )
    signature = request.headers.get("X-Line-Signature")
    if not signature:
        raise HTTPException(status_code=400, detail="Missing LINE signature")
    body = (await request.body()).decode("utf-8")
    try:
        events = request.app.state.line_bot.parse(body, signature)
    except (InvalidSignatureError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid LINE signature") from exc
    for event in events:
        if not isinstance(event, MessageEvent) or not isinstance(
            event.message, TextMessageContent
        ):
            continue
        event_id = getattr(event, "webhook_event_id", None)
        if not event_id or not await request.app.state.webhooks.claim(event_id):
            continue
        background_tasks.add_task(process_text_event_safely, request, event, event_id)
    return {"status": "accepted"}
