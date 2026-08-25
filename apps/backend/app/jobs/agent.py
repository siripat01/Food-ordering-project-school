from __future__ import annotations

import logging

from taskiq import Context, TaskiqDepends

from app.core.taskiq import broker
from app.domain.jobs import TaskName
from app.jobs.context import bind_correlation_id, services_from

logger = logging.getLogger(__name__)

IDEMPOTENCY_SCOPE = TaskName.AGENT_PROCESS.value


@broker.task(task_name=TaskName.AGENT_PROCESS.value)
async def process_agent_message(
    line_user_id: str,
    message: str,
    reply_token: str | None = None,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
    context: Context = TaskiqDepends(),
) -> None:
    """Run the customer assistant for one inbound LINE message.

    ``line_user_id`` is the LINE identity from the signature-verified webhook.
    The agent service resolves it to the internal user itself; a task argument
    must never be able to choose another internal user.

    A completed-command marker suppresses later duplicate deliveries. The
    marker is written only after the full handler succeeds: a pre-execution
    durable claim would turn a worker crash into lost work. Destructive tools
    receive the same stable idempotency key and enforce their own idempotency.
    """

    bind_correlation_id(correlation_id, task_id=context.message.task_id)
    services = services_from(context)
    task_id = context.message.task_id
    key = idempotency_key or task_id
    logger.info(
        "agent_task_started",
        extra={
            "correlation_id": correlation_id,
            "task_id": task_id,
            "task_name": TaskName.AGENT_PROCESS.value,
        },
    )
    if await services.idempotency.is_completed(scope=IDEMPOTENCY_SCOPE, key=key):
        return

    await services.line_chat.handle_text_message(
        line_user_id=line_user_id,
        text=message,
        reply_token=reply_token,
        idempotency_key=key,
        correlation_id=correlation_id,
        show_loading=True,
    )
    await services.idempotency.mark_completed(scope=IDEMPOTENCY_SCOPE, key=key)
