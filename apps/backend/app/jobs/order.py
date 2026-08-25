from __future__ import annotations

import logging

from taskiq import Context, TaskiqDepends

from app.core.taskiq import broker
from app.domain.jobs import TaskName
from app.jobs.context import bind_correlation_id, services_from

logger = logging.getLogger(__name__)


@broker.task(task_name=TaskName.ORDER_PROCESS.value)
async def process_order(
    order_id: str,
    correlation_id: str | None = None,
    context: Context = TaskiqDepends(),
) -> None:
    """Post-commit processing for a newly created order."""

    bind_correlation_id(correlation_id, task_id=context.message.task_id)
    services = services_from(context)
    logger.info(
        "order_task_started",
        extra={
            "correlation_id": correlation_id,
            "order_id": order_id,
            "task_id": context.message.task_id,
            "task_name": TaskName.ORDER_PROCESS.value,
        },
    )
    await services.order_workflow.process_created(order_id, correlation_id=correlation_id)


@broker.task(task_name=TaskName.ORDER_UPDATE_STATUS.value)
async def update_order_status(
    order_id: str,
    correlation_id: str | None = None,
    context: Context = TaskiqDepends(),
) -> None:
    """Propagate a committed order status change to the customer."""

    bind_correlation_id(correlation_id, task_id=context.message.task_id)
    services = services_from(context)
    logger.info(
        "order_task_started",
        extra={
            "correlation_id": correlation_id,
            "order_id": order_id,
            "task_id": context.message.task_id,
            "task_name": TaskName.ORDER_UPDATE_STATUS.value,
        },
    )
    await services.order_workflow.process_status_change(
        order_id,
        correlation_id=correlation_id,
    )


@broker.task(task_name=TaskName.ORDER_CANCEL.value)
async def cancel_order(
    order_id: str,
    user_id: str,
    correlation_id: str | None = None,
    context: Context = TaskiqDepends(),
) -> None:
    """Asynchronously cancel an order the authenticated customer owns."""

    bind_correlation_id(correlation_id, task_id=context.message.task_id)
    services = services_from(context)
    logger.info(
        "order_task_started",
        extra={
            "correlation_id": correlation_id,
            "order_id": order_id,
            "task_id": context.message.task_id,
            "task_name": TaskName.ORDER_CANCEL.value,
        },
    )
    await services.order_workflow.cancel(
        order_id,
        user_id=user_id,
        correlation_id=correlation_id,
    )
