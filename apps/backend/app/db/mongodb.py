from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection

from app.core.config import Settings


@dataclass(slots=True)
class MongoDatabase:
    settings: Settings
    client: AsyncMongoClient[dict[str, Any]] | None = None
    ready: bool = False

    async def connect(self) -> None:
        self.client = AsyncMongoClient(
            self.settings.mongodb_uri.get_secret_value(),
            serverSelectionTimeoutMS=5_000,
            tz_aware=True,
            appname="food-ordering-api",
        )
        await self.client.admin.command("ping")
        await self.ensure_indexes()
        self.ready = True

    async def close(self) -> None:
        self.ready = False
        if self.client is not None:
            await self.client.close()
            self.client = None

    def _require_client(self) -> AsyncMongoClient[dict[str, Any]]:
        if self.client is None:
            raise RuntimeError("MongoDB is not initialized")
        return self.client

    @property
    def users(self) -> AsyncCollection[dict[str, Any]]:
        return self._require_client()[self.settings.mongodb_users_database]["users"]

    @property
    def oauth_states(self) -> AsyncCollection[dict[str, Any]]:
        return self._require_client()[self.settings.mongodb_users_database]["line_oauth"]

    @property
    def webhook_events(self) -> AsyncCollection[dict[str, Any]]:
        return self._require_client()[self.settings.mongodb_users_database][
            "line_webhook_events"
        ]

    @property
    def products(self) -> AsyncCollection[dict[str, Any]]:
        return self._require_client()[self.settings.mongodb_products_database]["products"]

    @property
    def orders(self) -> AsyncCollection[dict[str, Any]]:
        return self._require_client()[self.settings.mongodb_orders_database]["orders"]

    async def ensure_indexes(self) -> None:
        await self.users.create_index(
            [("line_user_id", ASCENDING)],
            unique=True,
            partialFilterExpression={"line_user_id": {"$type": "string"}},
            name="uniq_line_user_id",
        )
        await self.oauth_states.create_index(
            [("stateHash", ASCENDING)], unique=True, name="uniq_oauth_state_hash"
        )
        await self.oauth_states.create_index(
            [("expiresAt", ASCENDING)], expireAfterSeconds=0, name="ttl_oauth_state"
        )
        await self.webhook_events.create_index(
            [("eventId", ASCENDING)], unique=True, name="uniq_line_webhook_event"
        )
        await self.webhook_events.create_index(
            [("createdAt", ASCENDING)],
            expireAfterSeconds=604_800,
            name="ttl_line_webhook_event",
        )
        await self.products.create_index(
            [("status", ASCENDING), ("createdAt", DESCENDING)],
            name="products_status_created",
        )
        await self.orders.create_index(
            [("userId", ASCENDING), ("createdAt", DESCENDING)],
            name="orders_user_created",
        )
        await self.orders.create_index(
            [("status", ASCENDING), ("createdAt", ASCENDING)],
            name="orders_status_created",
        )
        await self.orders.create_index(
            [("userId", ASCENDING), ("status", ASCENDING), ("createdAt", DESCENDING)],
            name="orders_active_user",
        )
        await self.orders.create_index(
            [("userId", ASCENDING), ("idempotencyKey", ASCENDING)],
            unique=True,
            partialFilterExpression={"idempotencyKey": {"$type": "string"}},
            name="uniq_order_idempotency",
        )

    async def ping(self) -> bool:
        try:
            await self._require_client().admin.command("ping")
        except Exception:
            self.ready = False
            return False
        self.ready = True
        return True
