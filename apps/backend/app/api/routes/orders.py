from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status

from app.api.dependencies import (
    CurrentUserDependency,
    CustomerDependency,
    get_order_service,
)
from app.domain.errors import NotFoundError
from app.domain.orders import OrderCreate, OrderListResponse, OrderResponse, OrderStatus
from app.domain.users import Role
from app.services.orders import OrderService

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    payload: OrderCreate,
    response: Response,
    customer: CustomerDependency,
    orders: Annotated[OrderService, Depends(get_order_service)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
) -> OrderResponse:
    order, created = await orders.create(
        user_id=customer.id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return order


@router.get("/me", response_model=OrderListResponse)
async def list_my_orders(
    customer: CustomerDependency,
    orders: Annotated[OrderService, Depends(get_order_service)],
    order_status: OrderStatus | None = None,
) -> OrderListResponse:
    return await orders.list_own(user_id=customer.id, status=order_status)


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: str,
    current_user: CurrentUserDependency,
    orders: Annotated[OrderService, Depends(get_order_service)],
) -> OrderResponse:
    order = await orders.get(order_id)
    if current_user.role is Role.CUSTOMER and order.user_id != current_user.id:
        raise NotFoundError("Order not found")
    return order


@router.post("/{order_id}/cancel", response_model=OrderResponse)
async def cancel_order(
    order_id: str,
    customer: CustomerDependency,
    orders: Annotated[OrderService, Depends(get_order_service)],
) -> OrderResponse:
    return await orders.cancel_own(order_id=order_id, user_id=customer.id)
