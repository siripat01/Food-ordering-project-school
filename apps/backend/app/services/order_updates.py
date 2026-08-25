from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.domain.common import utc_now
from app.domain.orders import OrderResponse, OrderStatus
from app.services.recommendations import RecommendationService
from app.services.users import UserService

logger = logging.getLogger(__name__)


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
        if not self.settings.line_enabled:
            return None
        return await self.users.get_line_user_id(order.user_id)

    def build_status_messages(self, order: OrderResponse) -> list[dict[str, Any]]:
        if order.status not in self.NOTIFIABLE_STATUSES:
            return []
        headline = self.STATUS_MESSAGES[order.status]
        return [{"type": "text", "text": f"{headline}\n{self._reference(order)}"}]

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
        recommendations: RecommendationService,
    ) -> None:
        self.broker = broker
        self.recommendations = recommendations
        self._tasks: set[asyncio.Task[None]] = set()

    def publish(self, order: OrderResponse) -> None:
        self.broker.publish(order)
        if order.status is OrderStatus.COMPLETED:
            task = asyncio.create_task(
                self._record_purchase_safely(order),
                name=f"recommendation-purchase-{order.id}",
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _record_purchase_safely(self, order: OrderResponse) -> None:
        try:
            await self.recommendations.record_purchase(order)
        except Exception:
            logger.warning(
                "recommendation_purchase_record_failed",
                extra={"order_id": order.id, "order_status": order.status.value},
            )

    async def close(self) -> None:
        if not self._tasks:
            return
        await asyncio.gather(*tuple(self._tasks), return_exceptions=True)


def heartbeat_payload() -> dict[str, str]:
    return {"timestamp": utc_now().isoformat()}
