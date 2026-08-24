from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.domain.common import parse_object_id
from app.domain.orders import OrderCreate, OrderResponse, OrderStatus, OrderStatusUpdate
from app.domain.products import ProductResponse
from app.domain.users import CurrentUser
from app.services.orders import OrderService
from app.services.products import ProductService

ToolCoroutine = Callable[..., Awaitable[str]]
ArgumentValidator = Callable[[dict[str, Any]], dict[str, Any]]


def _compact_text(value: str | None, *, limit: int) -> str | None:
    if value is None:
        return None
    return " ".join(value.split())[:limit]


def _product_data(product: ProductResponse) -> dict[str, Any]:
    return {
        "id": product.id,
        "name": _compact_text(product.name, limit=150),
        "price": float(product.price),
        "status": product.status.value,
        "description": _compact_text(product.description, limit=500),
        "addons": [
            {
                "id": addon.id,
                "name": _compact_text(addon.name, limit=100),
                "price": float(addon.price),
                "available": addon.available,
            }
            for addon in product.addons
        ],
    }


def _order_data(order: OrderResponse) -> dict[str, Any]:
    return {
        "id": order.id,
        "items": [
            {
                "productId": item.product_id,
                "productName": _compact_text(item.product_name_snapshot, limit=150),
                "unitPrice": float(item.unit_price),
                "quantity": item.quantity,
                "addons": [
                    {
                        "id": addon.id,
                        "name": _compact_text(addon.name, limit=100),
                        "price": float(addon.price),
                    }
                    for addon in item.addons
                ],
                "lineTotal": float(item.line_total),
            }
            for item in order.items
        ],
        "subtotal": float(order.subtotal),
        "total": float(order.total),
        "status": order.status.value,
        "createdAt": order.created_at.isoformat(),
        "updatedAt": order.updated_at.isoformat(),
        "completedAt": order.completed_at.isoformat() if order.completed_at else None,
        "cancelledAt": order.cancelled_at.isoformat() if order.cancelled_at else None,
    }


def _agent_payload(data_type: str, data: Any) -> str:
    return json.dumps(
        {"dataType": data_type, "data": data},
        ensure_ascii=False,
        separators=(",", ":"),
    )


@dataclass(frozen=True, slots=True)
class ScopedTool:
    name: str
    description: str
    coroutine: ToolCoroutine
    requires_confirmation: bool = False
    argument_validator: ArgumentValidator | None = None

    def validated_arguments(self, arguments: Any) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object")
        if self.argument_validator is None:
            return arguments
        return self.argument_validator(arguments)


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
            products = await self.products.list_available()
            return _agent_payload(
                "catalog",
                [_product_data(product) for product in products.products],
            )

        async def create_own_order(items: list[dict[str, Any]]) -> str:
            """Create an own order after deterministic customer confirmation."""
            payload = OrderCreate.model_validate({"items": items})
            order, _created = await self.orders.create(
                user_id=identity.id,
                payload=payload,
                idempotency_key=idempotency_key,
            )
            return _agent_payload("order", _order_data(order))

        async def view_own_orders(status: str | None = None) -> str:
            """View orders owned by the authenticated customer."""
            parsed_status = OrderStatus(status) if status else None
            orders = await self.orders.list_own(user_id=identity.id, status=parsed_status)
            return _agent_payload(
                "orders",
                [_order_data(order) for order in orders.orders],
            )

        async def cancel_eligible_own_order(order_id: str) -> str:
            """Cancel an eligible own order after deterministic customer confirmation."""
            order = await self.orders.cancel_own(order_id=order_id, user_id=identity.id)
            return _agent_payload("order", _order_data(order))

        def validate_create(arguments: dict[str, Any]) -> dict[str, Any]:
            payload = OrderCreate.model_validate({"items": arguments.get("items")})
            return {"items": [item.model_dump() for item in payload.items]}

        def validate_cancel(arguments: dict[str, Any]) -> dict[str, Any]:
            order_id = str(arguments.get("order_id") or "")
            parse_object_id(order_id)
            return {"order_id": order_id}

        return [
            ScopedTool("list_products", list_products.__doc__ or "", list_products),
            ScopedTool(
                "create_own_order",
                create_own_order.__doc__ or "",
                create_own_order,
                requires_confirmation=True,
                argument_validator=validate_create,
            ),
            ScopedTool("view_own_orders", view_own_orders.__doc__ or "", view_own_orders),
            ScopedTool(
                "cancel_eligible_own_order",
                cancel_eligible_own_order.__doc__ or "",
                cancel_eligible_own_order,
                requires_confirmation=True,
                argument_validator=validate_cancel,
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
