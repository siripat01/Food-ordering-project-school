from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, field_validator

from app.domain.common import APIModel, Money
from app.domain.users import Role


class OrderStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PREPARING = "preparing"
    READY = "ready"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


LEGACY_STATUS_MAP = {
    "pending": OrderStatus.PENDING,
    "confirmed": OrderStatus.CONFIRMED,
    "making": OrderStatus.PREPARING,
    "preparing": OrderStatus.PREPARING,
    "ready": OrderStatus.READY,
    "complete": OrderStatus.COMPLETED,
    "completed": OrderStatus.COMPLETED,
    "finished": OrderStatus.COMPLETED,
    "cancelled": OrderStatus.CANCELLED,
    "canceled": OrderStatus.CANCELLED,
}


ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING: frozenset({OrderStatus.CONFIRMED, OrderStatus.CANCELLED}),
    OrderStatus.CONFIRMED: frozenset({OrderStatus.PREPARING, OrderStatus.CANCELLED}),
    OrderStatus.PREPARING: frozenset({OrderStatus.READY, OrderStatus.CANCELLED}),
    OrderStatus.READY: frozenset({OrderStatus.COMPLETED}),
    OrderStatus.COMPLETED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}


class OrderItemRequest(APIModel):
    product_id: str = Field(min_length=24, max_length=24)
    quantity: int = Field(ge=1, le=20)
    addon_ids: list[str] = Field(default_factory=list, max_length=20)
    note: str | None = Field(default=None, max_length=300)

    @field_validator("addon_ids")
    @classmethod
    def unique_addons(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Duplicate addon IDs are not allowed")
        return value


class OrderCreate(APIModel):
    items: list[OrderItemRequest] = Field(min_length=1, max_length=20)


class OrderAddonSnapshot(APIModel):
    id: str
    name: str
    price: Money = Field(ge=Decimal("0"))


class OrderItemResponse(APIModel):
    product_id: str | None = None
    product_name_snapshot: str
    unit_price: Money = Field(ge=Decimal("0"))
    quantity: int = Field(ge=1)
    addons: list[OrderAddonSnapshot] = Field(default_factory=list)
    note: str | None = None
    line_total: Money = Field(ge=Decimal("0"))


class StatusHistoryEntry(APIModel):
    status: OrderStatus
    changed_at: datetime
    actor_id: str | None = None
    actor_role: Role | None = None


class OrderResponse(APIModel):
    id: str
    user_id: str
    items: list[OrderItemResponse]
    subtotal: Money
    total: Money
    status: OrderStatus
    status_history: list[StatusHistoryEntry]
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    schema_version: int = 2
    legacy_price_unverified: bool = False


class OrderListResponse(APIModel):
    orders: list[OrderResponse]


class OrderStatusUpdate(APIModel):
    status: OrderStatus
