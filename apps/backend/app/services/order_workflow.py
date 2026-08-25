from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.domain.orders import OrderResponse
from app.services.order_updates import LineOrderStatusNotifier
from app.services.orders import OrderService

logger = logging.getLogger(__name__)

#: Injected so the workflow service never imports a task module, which would
#: make a domain service depend on the Redis broker implementation.
PushMessages = Callable[[str, list[dict[str, Any]], str | None], Awaitable[None]]


class OrderWorkflowService:
    """Background order work triggered by committed order facts.

    Task handlers stay thin by delegating here; this service owns the decision
    of what a committed order change should cause, and nothing about transport.
    """

    def __init__(
        self,
        *,
        orders: OrderService,
        notifier: LineOrderStatusNotifier,
        push_messages: PushMessages,
    ) -> None:
        self.orders = orders
        self.notifier = notifier
        self.push_messages = push_messages

    async def _notify(
        self,
        order: OrderResponse,
        messages: list[dict[str, Any]],
        *,
        correlation_id: str | None,
    ) -> None:
        if not messages:
            return
        recipient = await self.notifier.resolve_recipient(order)
        if recipient is None:
            return
        await self.push_messages(recipient, messages, correlation_id)

    async def process_created(self, order_id: str, *, correlation_id: str | None) -> None:
        """Acknowledge a newly created order to the customer over LINE."""
        order = await self.orders.get(order_id)
        await self._notify(
            order,
            self.notifier.build_created_messages(order),
            correlation_id=correlation_id,
        )
        logger.info(
            "order_processed",
            extra={
                "correlation_id": correlation_id,
                "order_id": order.id,
                "order_status": order.status.value,
            },
        )

    async def process_status_change(self, order_id: str, *, correlation_id: str | None) -> None:
        """Notify the customer about a committed status change.

        The current status is re-read from MongoDB rather than trusted from the
        event payload, so a duplicate or late delivery cannot announce a stale
        status.
        """
        order = await self.orders.get(order_id)
        await self._notify(
            order,
            self.notifier.build_status_messages(order),
            correlation_id=correlation_id,
        )
        logger.info(
            "order_status_processed",
            extra={
                "correlation_id": correlation_id,
                "order_id": order.id,
                "order_status": order.status.value,
            },
        )

    async def cancel(self, order_id: str, *, user_id: str, correlation_id: str | None) -> None:
        """Asynchronous cancellation command.

        Cancellation itself stays in :class:`OrderService`, which enforces the
        allowed transitions and writes the resulting fact to the outbox.
        """
        order = await self.orders.cancel_own(order_id=order_id, user_id=user_id)
        logger.info(
            "order_cancelled",
            extra={
                "correlation_id": correlation_id,
                "order_id": order.id,
                "order_status": order.status.value,
            },
        )
