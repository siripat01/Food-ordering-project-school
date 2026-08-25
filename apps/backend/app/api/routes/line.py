from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from app.domain.jobs import TaskName
from app.jobs.agent import process_agent_message

router = APIRouter(prefix="/line", tags=["line"])
logger = logging.getLogger(__name__)


@router.post("/webhook")
async def line_webhook(request: Request) -> dict[str, str]:
    """Verify and enqueue inbound LINE text events.

    LINE may retry the same delivery. We deliberately enqueue before any durable
    dedupe decision so a crash cannot create a "claimed but never queued" gap.
    ``agent.process`` uses the LINE webhook event id as its idempotency key, so
    repeated enqueue is safe and preserves at-least-once processing.
    """

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

    correlation_id = request.state.request_id
    for event in events:
        if not isinstance(event, MessageEvent) or not isinstance(
            event.message,
            TextMessageContent,
        ):
            continue

        event_id = getattr(event, "webhook_event_id", None)
        line_user_id = getattr(event.source, "user_id", None)
        if not event_id or not line_user_id:
            continue

        try:
            await process_agent_message.kiq(
                line_user_id=line_user_id,
                message=event.message.text,
                reply_token=event.reply_token,
                idempotency_key=f"line:{event_id}",
                correlation_id=correlation_id,
            )
        except Exception as exc:
            logger.error(
                "line_event_enqueue_failed",
                extra={
                    "correlation_id": correlation_id,
                    "error_type": type(exc).__name__,
                    "task_name": TaskName.AGENT_PROCESS.value,
                },
            )
            # Returning non-2xx asks LINE to retry the webhook. Already-enqueued
            # events may therefore be seen again; agent.process is idempotent.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Unable to queue LINE event",
            ) from exc

        logger.info(
            "line_event_queued",
            extra={
                "correlation_id": correlation_id,
                "task_name": TaskName.AGENT_PROCESS.value,
            },
        )

    return {"status": "accepted"}
