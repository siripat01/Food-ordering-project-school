from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.domain.common import utc_now
from app.domain.orders import OrderResponse, OrderStatus
from app.services.users import UserService


@dataclass(frozen=True, slots=True)
class OrderEvent:
    id: str
    name: str
    order: OrderResponse


class OrderEventBroker:
    """Bounded in-process fan-out for authenticated staff SSE subscribers."""

    def __init__(self, *, queue_size: int) -> None:
        self.queue_size = queue_size
        self._subscribers: set[asyncio.Queue[OrderEvent]] = set()

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[OrderEvent]]:
        queue: asyncio.Queue[OrderEvent] = asyncio.Queue(maxsize=self.queue_size)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    def publish(self, order: OrderResponse) -> None:
        event = OrderEvent(
            id=uuid4().hex,
            name="order.updated",
            order=order,
        )
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)


class LineOrderStatusNotifier:
    """Decides which committed order changes deserve a LINE message, and what it says.

    Delivery itself is not done here. The ``order.update_status`` task enqueues
    ``line.push`` with the messages this class builds, so a LINE outage becomes a
    retryable job instead of a lost notification.
    """

    NOTIFIABLE_STATUSES = frozenset(
        {
            OrderStatus.CONFIRMED,
            OrderStatus.PREPARING,
            OrderStatus.READY,
            OrderStatus.COMPLETED,
            OrderStatus.CANCELLED,
        }
    )
    STATUS_MESSAGES = {
        OrderStatus.CONFIRMED: "ร้านยืนยันออเดอร์แล้ว",
        OrderStatus.PREPARING: "ร้านกำลังเตรียมออเดอร์",
        OrderStatus.READY: "ออเดอร์พร้อมรับแล้ว",
        OrderStatus.COMPLETED: "ออเดอร์เสร็จสมบูรณ์แล้ว",
        OrderStatus.CANCELLED: "ออเดอร์ถูกยกเลิกแล้ว",
    }
    CREATED_MESSAGE = "รับออเดอร์แล้ว กำลังรอร้านยืนยัน"

    def __init__(
        self,
        *,
        settings: Settings,
        users: UserService,
    ) -> None:
        self.settings = settings
        self.users = users

    @staticmethod
    def _reference(order: OrderResponse) -> str:
        return f"Order #{order.id[-8:].upper()}"

    async def resolve_recipient(self, order: OrderResponse) -> str | None:
        """Return the customer's LINE identity, from trusted server state only."""
        return await self.resolve_recipient_for_user(order.user_id)

    async def resolve_recipient_for_user(self, user_id: str) -> str | None:
        """Resolve a customer's LINE identity from trusted server state."""
        if not self.settings.line_enabled:
            return None
        return await self.users.get_line_user_id(user_id)

    def build_status_messages(self, order: OrderResponse) -> list[dict[str, Any]]:
        return self.build_status_messages_for_snapshot(
            order_id=order.id,
            status=order.status.value,
        )

    def build_status_messages_for_snapshot(
        self,
        *,
        order_id: str,
        status: str,
    ) -> list[dict[str, Any]]:
        order_status = OrderStatus(status)
        if order_status not in self.NOTIFIABLE_STATUSES:
            return []
        headline = self.STATUS_MESSAGES[order_status]
        reference = f"Order #{order_id[-8:].upper()}"
        return [{"type": "text", "text": f"{headline}\n{reference}"}]

    def build_created_messages(self, order: OrderResponse) -> list[dict[str, Any]]:
        return [{"type": "text", "text": f"{self.CREATED_MESSAGE}\n{self._reference(order)}"}]


class OrderUpdateDispatcher:
    """Publishes committed order changes to in-process consumers.

    Only work that must stay inside the API process lives here: the SSE fan-out
    is per-process by nature. Anything that has to survive a crash goes through
    the transactional outbox instead.
    """

    def __init__(
        self,
        *,
        broker: OrderEventBroker,
    ) -> None:
        self.broker = broker

    def publish(self, order: OrderResponse) -> None:
        self.broker.publish(order)


def heartbeat_payload() -> dict[str, str]:
    return {"timestamp": utc_now().isoformat()}
