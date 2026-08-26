from __future__ import annotations

import logging
from typing import Any

from taskiq import Context, TaskiqDepends

from app.core.taskiq import broker
from app.domain.jobs import TaskName
from app.jobs.context import bind_correlation_id, services_from

logger = logging.getLogger(__name__)


def _masked_line_user_id(line_user_id: str) -> str:
    if len(line_user_id) <= 12:
        return "***"
    return f"{line_user_id[:6]}...{line_user_id[-6:]}"


@broker.task(task_name=TaskName.LINE_PUSH.value)
async def push_line(
    line_user_id: str,
    messages: list[dict[str, Any]],
    correlation_id: str | None = None,
    context: Context = TaskiqDepends(),
) -> None:
    """Proactive LINE message addressed to a LINE user id."""

    bind_correlation_id(correlation_id, task_id=context.message.task_id)
    services = services_from(context)
    common = {
        "correlation_id": correlation_id,
        "task_id": context.message.task_id,
        "task_name": TaskName.LINE_PUSH.value,
        "line_user_id_masked": _masked_line_user_id(line_user_id),
        "line_user_id_length": len(line_user_id),
    }
    logger.info("line_push_task_started", extra=common)
    try:
        await services.line_bot.push_messages(line_user_id=line_user_id, messages=messages)
    except Exception as exc:
        logger.exception(
            "line_push_task_failed",
            extra={
                **common,
                "error_type": type(exc).__name__,
                "upstream_status": getattr(exc, "status_code", None),
            },
        )
        raise
    logger.info("line_push_task_succeeded", extra=common)


@broker.task(task_name=TaskName.LINE_REPLY.value)
async def reply_line(
    reply_token: str,
    messages: list[dict[str, Any]],
    correlation_id: str | None = None,
    context: Context = TaskiqDepends(),
) -> None:
    """Response to an inbound LINE webhook, addressed by one-time reply token.

    Reply tokens expire quickly and are single-use, so a retried delivery is
    expected to fail upstream rather than duplicate a message.
    """

    bind_correlation_id(correlation_id, task_id=context.message.task_id)
    services = services_from(context)
    common = {
        "correlation_id": correlation_id,
        "task_id": context.message.task_id,
        "task_name": TaskName.LINE_REPLY.value,
    }
    logger.info("line_reply_task_started", extra=common)
    try:
        await services.line_bot.reply_messages(reply_token=reply_token, messages=messages)
    except Exception as exc:
        logger.exception(
            "line_reply_task_failed",
            extra={
                **common,
                "error_type": type(exc).__name__,
                "upstream_status": getattr(exc, "status_code", None),
            },
        )
        raise
    logger.info("line_reply_task_succeeded", extra=common)
