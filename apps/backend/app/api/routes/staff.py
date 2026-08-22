from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import StaffDependency, get_order_service
from app.domain.orders import (
    OrderListResponse,
    OrderResponse,
    OrderStatus,
    OrderStatusUpdate,
)
from app.services.orders import OrderService

router = APIRouter(prefix="/staff/orders", tags=["staff"])


@router.get("", response_model=OrderListResponse)
async def list_order_queue(
    _staff: StaffDependency,
    orders: Annotated[OrderService, Depends(get_order_service)],
    order_status: OrderStatus | None = None,
) -> OrderListResponse:
    return await orders.list_queue(status=order_status)


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
