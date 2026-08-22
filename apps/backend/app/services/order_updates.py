from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import uuid4

from app.core.config import Settings
from app.domain.common import utc_now
from app.domain.orders import OrderResponse, OrderStatus
from app.integrations.line import LineBotClient
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

    def __init__(
        self,
        *,
        settings: Settings,
        users: UserService,
        line_bot: LineBotClient,
    ) -> None:
        self.settings = settings
        self.users = users
        self.line_bot = line_bot

    async def notify(self, order: OrderResponse) -> None:
        if not self.settings.line_enabled or order.status not in self.NOTIFIABLE_STATUSES:
            return
        line_user_id = await self.users.get_line_user_id(order.user_id)
        if line_user_id is None:
            return
        message = self.STATUS_MESSAGES[order.status]
        try:
            await self.line_bot.push_text(
                line_user_id=line_user_id,
                text=f"{message}\nOrder #{order.id[-8:].upper()}",
            )
        except Exception:
            logger.warning(
                "line_order_status_notification_failed",
                extra={"order_id": order.id, "order_status": order.status.value},
            )


class OrderUpdateDispatcher:
    """Publishes committed order changes and owns background notification tasks."""

    def __init__(
        self,
        *,
        broker: OrderEventBroker,
        notifier: LineOrderStatusNotifier,
        recommendations: RecommendationService,
    ) -> None:
        self.broker = broker
        self.notifier = notifier
        self.recommendations = recommendations
        self._tasks: set[asyncio.Task[None]] = set()

    def publish(self, order: OrderResponse) -> None:
        self.broker.publish(order)
        if order.status in self.notifier.NOTIFIABLE_STATUSES:
            task = asyncio.create_task(
                self.notifier.notify(order),
                name=f"line-order-notification-{order.id}",
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
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
