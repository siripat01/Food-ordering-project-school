from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.domain.orders import OrderCreate, OrderStatus, OrderStatusUpdate
from app.domain.users import CurrentUser
from app.services.orders import OrderService
from app.services.products import ProductService

ToolCoroutine = Callable[..., Awaitable[str]]


@dataclass(frozen=True, slots=True)
class ScopedTool:
    name: str
    description: str
    coroutine: ToolCoroutine


class CustomerToolFactory:
    TOOL_NAMES = frozenset(
        {
            "list_products",
            "create_own_order",
            "view_own_orders",
            "cancel_eligible_own_order",
        }
    )

    def __init__(self, products: ProductService, orders: OrderService) -> None:
        self.products = products
        self.orders = orders

    def build(self, *, identity: CurrentUser, idempotency_key: str) -> list[ScopedTool]:
        async def list_products() -> str:
            """List currently available products and their server prices."""
            return (await self.products.list_available()).model_dump_json()

        async def create_own_order(items: list[dict[str, Any]]) -> str:
            """Create an order for the authenticated customer using product IDs."""
            payload = OrderCreate.model_validate({"items": items})
            order, _created = await self.orders.create(
                user_id=identity.id,
                payload=payload,
                idempotency_key=idempotency_key,
            )
            return order.model_dump_json()

        async def view_own_orders(status: str | None = None) -> str:
            """View orders owned by the authenticated customer."""
            parsed_status = OrderStatus(status) if status else None
            return (
                await self.orders.list_own(user_id=identity.id, status=parsed_status)
            ).model_dump_json()

        async def cancel_eligible_own_order(order_id: str) -> str:
            """Cancel an eligible order owned by the authenticated customer."""
            return (
                await self.orders.cancel_own(order_id=order_id, user_id=identity.id)
            ).model_dump_json()

        return [
            ScopedTool("list_products", list_products.__doc__ or "", list_products),
            ScopedTool("create_own_order", create_own_order.__doc__ or "", create_own_order),
            ScopedTool("view_own_orders", view_own_orders.__doc__ or "", view_own_orders),
            ScopedTool(
                "cancel_eligible_own_order",
                cancel_eligible_own_order.__doc__ or "",
                cancel_eligible_own_order,
            ),
        ]


class StaffToolFactory:
    TOOL_NAMES = frozenset({"view_order_queue", "update_order_status"})

    def __init__(self, orders: OrderService) -> None:
        self.orders = orders

    def build(self, *, identity: CurrentUser) -> list[ScopedTool]:
        async def view_order_queue(status: str | None = None) -> str:
            """View the operational order queue."""
            parsed_status = OrderStatus(status) if status else None
            return (await self.orders.list_queue(status=parsed_status)).model_dump_json()

        async def update_order_status(order_id: str, status: str) -> str:
            """Apply a valid operational status transition to an order."""
            payload = OrderStatusUpdate(status=OrderStatus(status))
            return (
                await self.orders.transition(
                    order_id=order_id,
                    new_status=payload.status,
                    actor_id=identity.id,
                    actor_role=identity.role,
                )
            ).model_dump_json()

        return [
            ScopedTool("view_order_queue", view_order_queue.__doc__ or "", view_order_queue),
            ScopedTool(
                "update_order_status",
                update_order_status.__doc__ or "",
                update_order_status,
            ),
        ]
