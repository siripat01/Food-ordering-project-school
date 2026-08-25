from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

from app.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MongoDatabase:
    settings: Settings
    client: AsyncMongoClient[dict[str, Any]] | None = None
    ready: bool = False
    transactions_supported: bool = field(default=False, init=False)

    async def connect(self) -> None:
        self.client = AsyncMongoClient(
            self.settings.mongodb_uri.get_secret_value(),
            serverSelectionTimeoutMS=5_000,
            tz_aware=True,
            appname="food-ordering-api",
        )
        await self.client.admin.command("ping")
        await self._detect_transaction_support()
        await self.ensure_indexes()
        self.ready = True

    async def _detect_transaction_support(self) -> None:
        """Multi-document transactions require a replica set or a sharded cluster.

        A standalone ``mongod`` silently rejects them, so the transactional
        outbox degrades to a documented non-atomic fallback instead of failing
        startup. See ``docs/background-jobs.md``.
        """
        hello = await self._require_client().admin.command("hello")
        self.transactions_supported = (
            bool(hello.get("setName")) or hello.get("msg") == "isdbgrid"
        )
        if not self.transactions_supported:
            logger.warning(
                "mongodb_transactions_unavailable",
                extra={"error_type": "StandaloneDeployment"},
            )

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncClientSession | None]:
        """Yield a transactional session, or ``None`` on a standalone deployment.

        Callers must pass the yielded session to every write that has to commit
        together. When ``None`` is yielded the writes are *not* atomic; that
        limitation is deliberate and documented rather than hidden.
        """
        if not self.transactions_supported:
            yield None
            return
        async with self._require_client().start_session() as session:
            async with await session.start_transaction():
                yield session

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

    @property
    def recommendation_events(self) -> AsyncCollection[dict[str, Any]]:
        return self._require_client()[self.settings.mongodb_orders_database][
            "recommendation_events"
        ]

    @property
    def recommendation_slates(self) -> AsyncCollection[dict[str, Any]]:
        return self._require_client()[self.settings.mongodb_orders_database][
            "recommendation_slates"
        ]

    @property
    def recommendation_event_counters(self) -> AsyncCollection[dict[str, Any]]:
        return self._require_client()[self.settings.mongodb_orders_database][
            "recommendation_event_counters"
        ]

    @property
    def recommendation_model_versions(self) -> AsyncCollection[dict[str, Any]]:
        return self._require_client()[self.settings.mongodb_orders_database][
            "recommendation_model_versions"
        ]

    @property
    def recommendation_artifacts(self) -> AsyncCollection[dict[str, Any]]:
        return self._require_client()[self.settings.mongodb_orders_database][
            "recommendation_artifacts"
        ]

    @property
    def recommendation_model_state(self) -> AsyncCollection[dict[str, Any]]:
        return self._require_client()[self.settings.mongodb_orders_database][
            "recommendation_model_state"
        ]

    @property
    def recommendation_model_locks(self) -> AsyncCollection[dict[str, Any]]:
        return self._require_client()[self.settings.mongodb_orders_database][
            "recommendation_model_locks"
        ]

    @property
    def outbox_events(self) -> AsyncCollection[dict[str, Any]]:
        return self._require_client()[self.settings.mongodb_orders_database]["outbox_events"]

    @property
    def job_idempotency(self) -> AsyncCollection[dict[str, Any]]:
        return self._require_client()[self.settings.mongodb_orders_database]["job_idempotency"]

    @staticmethod
    async def _ensure_ttl_index(
        collection: AsyncCollection[dict[str, Any]],
        *,
        field: str,
        expire_after_seconds: int,
        name: str,
    ) -> None:
        """Create or update a named TTL index without dropping indexed data."""
        indexes = await collection.index_information()
        existing = indexes.get(name)
        expected_key = [(field, ASCENDING)]
        if existing is None:
            await collection.create_index(
                expected_key,
                expireAfterSeconds=expire_after_seconds,
                name=name,
            )
            return
        if existing.get("key") != expected_key:
            raise RuntimeError(
                f"TTL index {name} uses an unexpected key; run an explicit index migration"
            )
        if int(existing.get("expireAfterSeconds", -1)) == expire_after_seconds:
            return
        await collection.database.command(
            {
                "collMod": collection.name,
                "index": {
                    "name": name,
                    "expireAfterSeconds": expire_after_seconds,
                },
            }
        )

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
        await self._ensure_ttl_index(
            self.oauth_states,
            field="expiresAt",
            expire_after_seconds=0,
            name="ttl_oauth_state",
        )
        await self.webhook_events.create_index(
            [("eventId", ASCENDING)], unique=True, name="uniq_line_webhook_event"
        )
        await self._ensure_ttl_index(
            self.webhook_events,
            field="createdAt",
            expire_after_seconds=604_800,
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
            [("status", ASCENDING), ("completedAt", DESCENDING)],
            name="orders_completed_training",
        )
        await self.orders.create_index(
            [("userId", ASCENDING), ("status", ASCENDING), ("completedAt", DESCENDING)],
            name="orders_completed_user_profile",
        )
        await self.orders.create_index(
            [("userId", ASCENDING), ("idempotencyKey", ASCENDING)],
            unique=True,
            partialFilterExpression={"idempotencyKey": {"$type": "string"}},
            name="uniq_order_idempotency",
        )
        await self.orders.create_index(
            [("status", ASCENDING), ("completedAt", ASCENDING)],
            name="orders_completed_stream",
        )
        recommendation_indexes = await self.recommendation_events.index_information()
        if "uniq_recommendation_event" in recommendation_indexes:
            # R0 replaced client event IDs with a server-derived dedupe key. The old
            # unique eventId index would allow only one document with a missing field.
            await self.recommendation_events.drop_index("uniq_recommendation_event")
        await self.recommendation_events.create_index(
            [("dedupeKey", ASCENDING)],
            unique=True,
            partialFilterExpression={"dedupeKey": {"$type": "string"}},
            name="uniq_recommendation_event_dedupe",
        )
        await self._ensure_ttl_index(
            self.recommendation_events,
            field="createdAt",
            expire_after_seconds=self.settings.recommendation_event_retention_days * 86_400,
            name="ttl_recommendation_event",
        )
        await self.recommendation_events.create_index(
            [("userRef", ASCENDING), ("eventType", ASCENDING), ("createdAt", DESCENDING)],
            name="recommendation_user_type_created",
        )
        await self.recommendation_events.create_index(
            [("productId", ASCENDING), ("eventType", ASCENDING), ("createdAt", DESCENDING)],
            name="recommendation_product_type_created",
        )
        await self.recommendation_events.create_index(
            [("recommendationId", ASCENDING), ("userRef", ASCENDING), ("productId", ASCENDING)],
            name="recommendation_slate_user_product",
        )
        await self.recommendation_slates.create_index(
            [("userRef", ASCENDING), ("createdAt", DESCENDING)],
            name="recommendation_slates_user_created",
        )
        await self._ensure_ttl_index(
            self.recommendation_slates,
            field="expiresAt",
            expire_after_seconds=0,
            name="ttl_recommendation_slate",
        )
        await self._ensure_ttl_index(
            self.recommendation_event_counters,
            field="expiresAt",
            expire_after_seconds=0,
            name="ttl_recommendation_event_counter",
        )
        await self.recommendation_event_counters.create_index(
            [("userRef", ASCENDING)],
            name="recommendation_event_counters_user",
        )
        await self.recommendation_artifacts.create_index(
            [("modelVersion", ASCENDING), ("productId", ASCENDING)],
            unique=True,
            name="uniq_recommendation_artifact_model_product",
        )
        await self.recommendation_artifacts.create_index(
            [("modelVersion", ASCENDING)],
            name="recommendation_artifact_model",
        )
        await self.recommendation_model_versions.create_index(
            [("status", ASCENDING), ("builtAt", DESCENDING)],
            name="recommendation_model_status_built",
        )
        await self.recommendation_model_versions.create_index(
            [("builtAt", DESCENDING)],
            name="recommendation_model_built",
        )
        await self._ensure_ttl_index(
            self.recommendation_model_locks,
            field="expiresAt",
            expire_after_seconds=0,
            name="ttl_recommendation_model_lock",
        )
        await self.outbox_events.create_index(
            [("status", ASCENDING), ("availableAt", ASCENDING)],
            name="outbox_claimable",
        )
        await self.outbox_events.create_index(
            [("idempotencyKey", ASCENDING)],
            unique=True,
            partialFilterExpression={"idempotencyKey": {"$type": "string"}},
            name="uniq_outbox_idempotency",
        )
        await self._ensure_ttl_index(
            self.outbox_events,
            field="publishedAt",
            expire_after_seconds=604_800,
            name="ttl_outbox_published",
        )
        await self.job_idempotency.create_index(
            [("scope", ASCENDING), ("key", ASCENDING)],
            unique=True,
            name="uniq_job_idempotency",
        )
        await self._ensure_ttl_index(
            self.job_idempotency,
            field="expiresAt",
            expire_after_seconds=0,
            name="ttl_job_idempotency",
        )

    async def ping(self) -> bool:
        try:
            await self._require_client().admin.command("ping")
        except Exception:
            self.ready = False
            return False
        self.ready = True
        return True
