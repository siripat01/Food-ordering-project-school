from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable

from app.core.config import Settings, get_settings
from app.core.observability import configure_logging, set_request_id
from app.db.mongodb import MongoDatabase
from app.domain.jobs import TaskName
from app.domain.outbox import OutboxEvent, OutboxEventType
from app.services.outbox import (
    OutboxLeaseLostError,
    OutboxRepository,
    OutboxService,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 1.0
IDLE_POLL_INTERVAL_SECONDS = 3.0
CLAIM_BATCH_SIZE = 20

DispatchHandler = Callable[[OutboxEvent], Awaitable[None]]


async def _dispatch_order_created(event: OutboxEvent) -> None:
    from app.jobs.order import process_order

    await process_order.kiq(
        order_id=str(event.payload["orderId"]),
        correlation_id=event.correlation_id,
    )


async def _dispatch_order_status_changed(event: OutboxEvent) -> None:
    from app.jobs.order import update_order_status

    await update_order_status.kiq(
        order_id=str(event.payload["orderId"]),
        correlation_id=event.correlation_id,
    )


EVENT_HANDLERS: dict[str, DispatchHandler] = {
    OutboxEventType.ORDER_CREATED.value: _dispatch_order_created,
    OutboxEventType.ORDER_STATUS_CHANGED.value: _dispatch_order_status_changed,
}

EVENT_TASK_NAMES: dict[str, TaskName] = {
    OutboxEventType.ORDER_CREATED.value: TaskName.ORDER_PROCESS,
    OutboxEventType.ORDER_STATUS_CHANGED.value: TaskName.ORDER_UPDATE_STATUS,
}


class OutboxDispatcher:
    """Turn committed facts into Taskiq commands."""

    def __init__(
        self,
        outbox: OutboxService,
        *,
        handlers: dict[str, DispatchHandler] | None = None,
        batch_size: int = CLAIM_BATCH_SIZE,
    ) -> None:
        self.outbox = outbox
        self.handlers = handlers if handlers is not None else EVENT_HANDLERS
        self.batch_size = batch_size

    async def dispatch(self, event: OutboxEvent) -> None:
        """Enqueue one event's task, then transition the owned lease."""

        set_request_id(event.correlation_id or event.id)
        handler = self.handlers.get(event.event_type)
        if handler is None:
            await self.outbox.mark_as_dead(
                event,
                error=f"No task route for event type {event.event_type}",
            )
            return

        try:
            await handler(event)
        except Exception as exc:
            await self.outbox.mark_as_failed(
                event,
                error=f"{type(exc).__name__}: {exc}",
            )
            logger.warning(
                "outbox_dispatch_failed",
                extra={
                    "attempt": event.attempts,
                    "correlation_id": event.correlation_id,
                    "error_type": type(exc).__name__,
                    "event_id": event.id,
                    "event_type": event.event_type,
                },
            )
            return

        await self.outbox.mark_as_sent(event)
        logger.info(
            "outbox_event_dispatched",
            extra={
                "attempt": event.attempts,
                "correlation_id": event.correlation_id,
                "event_id": event.id,
                "event_type": event.event_type,
                "task_name": str(EVENT_TASK_NAMES.get(event.event_type, "")),
            },
        )

    async def run_once(self) -> int:
        """Claim and dispatch one batch. Return the number selected for dispatch."""

        events = await self.outbox.claim_pending_events(limit=self.batch_size)
        for event in events:
            try:
                await self.dispatch(event)
            except OutboxLeaseLostError:
                # Another dispatcher reclaimed this event after the visibility
                # deadline. The stale owner must never overwrite the new owner.
                logger.warning(
                    "outbox_lease_lost",
                    extra={
                        "attempt": event.attempts,
                        "event_id": event.id,
                        "event_type": event.event_type,
                    },
                )
            except Exception:
                logger.exception(
                    "outbox_dispatch_crashed",
                    extra={"event_id": event.id, "event_type": event.event_type},
                )
        return len(events)


class OutboxPollingDispatcher:
    """Poll MongoDB for due outbox events until stopped."""

    def __init__(
        self,
        dispatcher: OutboxDispatcher,
        *,
        poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
        idle_interval_seconds: float = IDLE_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.dispatcher = dispatcher
        self.poll_interval_seconds = poll_interval_seconds
        self.idle_interval_seconds = idle_interval_seconds
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        logger.info("outbox_dispatcher_started")
        while not self._stop.is_set():
            try:
                handled = await self.dispatcher.run_once()
            except Exception:
                logger.exception("outbox_poll_failed")
                handled = 0
            delay = self.poll_interval_seconds if handled else self.idle_interval_seconds
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
        logger.info("outbox_dispatcher_stopped")


async def main(settings: Settings | None = None) -> None:
    """Entrypoint for ``python -m app.jobs.dispatcher``."""

    resolved = settings or get_settings()
    configure_logging(level=resolved.log_level, json_logs=resolved.log_json)

    from app.core.taskiq import broker

    db = MongoDatabase(resolved)
    broker_started = False
    try:
        await db.connect()
        await broker.startup()
        broker_started = True

        polling = OutboxPollingDispatcher(
            OutboxDispatcher(OutboxService(OutboxRepository(db)))
        )
        loop = asyncio.get_running_loop()
        for received in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(received, polling.stop)
        await polling.run()
    finally:
        if broker_started:
            await broker.shutdown()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
