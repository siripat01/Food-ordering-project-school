from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, HttpUrl, field_validator

from app.domain.common import APIModel, Money


class ProductStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    DISCONTINUED = "discontinued"


class AddonInput(APIModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    price: Money = Field(ge=Decimal("0"))
    available: bool = True


class ProductCreate(APIModel):
    name: str = Field(min_length=1, max_length=150)
    price: Money = Field(ge=Decimal("0"))
    status: ProductStatus = ProductStatus.AVAILABLE
    description: str | None = Field(default=None, max_length=1000)
    image_url: HttpUrl | None = None
    addons: list[AddonInput] = Field(default_factory=list, max_length=30)

    @field_validator("addons")
    @classmethod
    def unique_addon_ids(cls, value: list[AddonInput]) -> list[AddonInput]:
        ids = [addon.id for addon in value]
        if len(ids) != len(set(ids)):
            raise ValueError("Addon IDs must be unique")
        return value


class ProductUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    price: Money | None = Field(default=None, ge=Decimal("0"))
    status: ProductStatus | None = None
    description: str | None = Field(default=None, max_length=1000)
    image_url: HttpUrl | None = None
    addons: list[AddonInput] | None = Field(default=None, max_length=30)


class ProductResponse(APIModel):
    id: str
    name: str
    price: Money
    status: ProductStatus
    description: str | None = None
    image_url: str | None = None
    addons: list[AddonInput] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProductListResponse(APIModel):
    products: list[ProductResponse]
