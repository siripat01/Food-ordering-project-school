import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.dependencies import (
    StaffDependency,
    get_order_event_broker,
    get_order_service,
    get_settings,
)
from app.core.config import Settings
from app.domain.orders import (
    OrderListResponse,
    OrderResponse,
    OrderStatus,
    OrderStatusUpdate,
)
from app.services.order_updates import OrderEventBroker, heartbeat_payload
from app.services.orders import OrderService

router = APIRouter(prefix="/staff/orders", tags=["staff"])


def _sse_message(*, event: str, data: Any, event_id: str | None = None) -> str:
    fields = []
    if event_id:
        fields.append(f"id: {event_id}")
    fields.append(f"event: {event}")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    fields.extend(f"data: {line}" for line in payload.splitlines() or [""])
    return "\n".join(fields) + "\n\n"


@router.get("", response_model=OrderListResponse)
async def list_order_queue(
    _staff: StaffDependency,
    orders: Annotated[OrderService, Depends(get_order_service)],
    order_status: OrderStatus | None = None,
    include_terminal: bool = False,
) -> OrderListResponse:
    return await orders.list_queue(
        status=order_status,
        include_terminal=include_terminal,
    )


@router.get("/stream")
async def stream_order_queue(
    request: Request,
    _staff: StaffDependency,
    orders: Annotated[OrderService, Depends(get_order_service)],
    broker: Annotated[OrderEventBroker, Depends(get_order_event_broker)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        async with broker.subscribe() as queue:
            snapshot = await orders.list_queue(include_terminal=True)
            yield "retry: 5000\n\n"
            yield _sse_message(
                event="snapshot",
                data={"orders": [order.model_dump(mode="json") for order in snapshot.orders]},
            )
            while not await request.is_disconnected():
                try:
                    update = await asyncio.wait_for(
                        queue.get(),
                        timeout=settings.sse_heartbeat_seconds,
                    )
                except TimeoutError:
                    yield _sse_message(event="heartbeat", data=heartbeat_payload())
                    continue
                yield _sse_message(
                    event=update.name,
                    event_id=update.id,
                    data={"order": update.order.model_dump(mode="json")},
                )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.patch("/{order_id}/status", response_model=OrderResponse)
async def update_order_status(
    order_id: str,
    payload: OrderStatusUpdate,
    staff: StaffDependency,
    orders: Annotated[OrderService, Depends(get_order_service)],
) -> OrderResponse:
    return await orders.transition(
        order_id=order_id,
        new_status=payload.status,
        actor_id=staff.id,
        actor_role=staff.role,
    )
