from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.domain.common import APIModel


class Role(StrEnum):
    CUSTOMER = "customer"
    STAFF = "staff"
    ADMIN = "admin"


LEGACY_ROLE_MAP = {
    "student": Role.CUSTOMER,
    "user": Role.CUSTOMER,
    "customer": Role.CUSTOMER,
    "staff": Role.STAFF,
    "admin": Role.ADMIN,
}


class CurrentUser(APIModel):
    id: str
    role: Role
    display_name: str = "LINE user"
    email: str | None = Field(default=None, max_length=320)
    picture_url: str | None = None
    active: bool = True


class UserResponse(CurrentUser):
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UserRoleUpdate(APIModel):
    role: Role


class UserActiveUpdate(APIModel):
    active: bool


class UserListResponse(APIModel):
    users: list[UserResponse]
    total: int = Field(ge=0)
