from __future__ import annotations

from typing import Any

from pymongo import ReturnDocument

from app.db.mongodb import MongoDatabase
from app.domain.common import parse_object_id, utc_now
from app.domain.errors import ConflictError, NotFoundError
from app.domain.users import (
    LEGACY_ROLE_MAP,
    CurrentUser,
    Role,
    UserResponse,
)


class UserService:
    def __init__(self, db: MongoDatabase) -> None:
        self.db = db

    @staticmethod
    def _role(value: Any) -> Role:
        return LEGACY_ROLE_MAP.get(str(value or "customer").lower(), Role.CUSTOMER)

    @classmethod
    def to_current_user(cls, doc: dict[str, Any]) -> CurrentUser:
        return CurrentUser(
            id=str(doc["_id"]),
            role=cls._role(doc.get("role")),
            display_name=str(doc.get("display_name") or doc.get("username") or "LINE user"),
            email=doc.get("email") or None,
            picture_url=doc.get("picture_url") or None,
            active=bool(doc.get("active", True)),
        )

    @classmethod
    def to_response(cls, doc: dict[str, Any]) -> UserResponse:
        current = cls.to_current_user(doc)
        return UserResponse(
            **current.model_dump(),
            created_at=doc.get("createdAt") or doc.get("created_at"),
            updated_at=doc.get("updatedAt") or doc.get("updated_at"),
        )

    async def get_by_id(self, user_id: str) -> CurrentUser | None:
        try:
            object_id = parse_object_id(user_id)
        except ValueError:
            return None
        doc = await self.db.users.find_one({"_id": object_id})
        return self.to_current_user(doc) if doc else None

    async def get_by_line_id(self, line_user_id: str) -> CurrentUser | None:
        doc = await self.db.users.find_one({"line_user_id": line_user_id})
        return self.to_current_user(doc) if doc else None

    async def upsert_line_user(
        self,
        *,
        line_user_id: str,
        display_name: str,
        picture_url: str | None,
        email: str | None,
    ) -> CurrentUser:
        now = utc_now()
        set_fields: dict[str, Any] = {
            "display_name": display_name[:200],
            "picture_url": picture_url,
            "email": email,
            "updatedAt": now,
        }
        doc = await self.db.users.find_one_and_update(
            {"line_user_id": line_user_id},
            {
                "$set": set_fields,
                "$setOnInsert": {
                    "line_user_id": line_user_id,
                    "role": Role.CUSTOMER.value,
                    "active": True,
                    "createdAt": now,
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            raise RuntimeError("User upsert did not return a document")
        return self.to_current_user(doc)

    async def list_users(self, *, limit: int = 100) -> list[UserResponse]:
        docs = await self.db.users.find({}).sort("createdAt", -1).limit(limit).to_list()
        return [self.to_response(doc) for doc in docs]

    async def update_role(self, *, user_id: str, role: Role, actor_id: str) -> UserResponse:
        if user_id == actor_id and role is not Role.ADMIN:
            raise ConflictError("Administrators cannot remove their own admin role")
        object_id = parse_object_id(user_id)
        doc = await self.db.users.find_one_and_update(
            {"_id": object_id},
            {"$set": {"role": role.value, "updatedAt": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            raise NotFoundError("User not found")
        return self.to_response(doc)

    async def update_active(self, *, user_id: str, active: bool, actor_id: str) -> UserResponse:
        if user_id == actor_id and not active:
            raise ConflictError("Administrators cannot deactivate their own account")
        object_id = parse_object_id(user_id)
        doc = await self.db.users.find_one_and_update(
            {"_id": object_id},
            {"$set": {"active": active, "updatedAt": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            raise NotFoundError("User not found")
        return self.to_response(doc)
