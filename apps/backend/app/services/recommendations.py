from __future__ import annotations

import hashlib
import hmac
import logging
from collections import defaultdict
from datetime import UTC, datetime, time, timedelta
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.config import Settings
from app.core.observability import ApplicationMetrics
from app.db.mongodb import MongoDatabase
from app.domain.common import utc_now
from app.domain.errors import ConflictError, NotFoundError
from app.domain.orders import OrderResponse, OrderStatus
from app.domain.products import ProductResponse
from app.domain.recommendations import (
    RecommendationEventCreate,
    RecommendationEventType,
    RecommendationResponse,
    RecommendationStrategy,
)
from app.services.products import ProductService
from app.services.recommendation_runtime import RecommendationModelRuntime

logger = logging.getLogger(__name__)

BASELINE_MODEL_VERSION = "baseline-v1"
DEFAULT_PLACEMENT = "customer-home"


class RecommendationService:
    def __init__(
        self,
        *,
        db: MongoDatabase,
        products: ProductService,
        settings: Settings,
        http_client: httpx.AsyncClient,
        metrics: ApplicationMetrics | None = None,
        runtime: RecommendationModelRuntime | None = None,
    ) -> None:
        self.db = db
        self.products = products
        self.settings = settings
        self.http_client = http_client
        self.metrics = metrics
        self.runtime = runtime

    def _key(self) -> bytes:
        return self.settings.recommendation_user_ref_secret.get_secret_value().encode()

    @staticmethod
    def _user_ref_with_key(user_id: str, key: bytes) -> str:
        return hmac.new(key, user_id.encode(), hashlib.sha256).hexdigest()

    def _user_ref(self, user_id: str) -> str:
        return self._user_ref_with_key(user_id, self._key())

    def _all_user_refs(self, user_id: str) -> set[str]:
        refs = {self._user_ref(user_id)}
        previous = getattr(
            self.settings,
            "recommendation_user_ref_previous_secrets",
            {},
        )
        refs.update(
            self._user_ref_with_key(user_id, secret.get_secret_value().encode())
            for secret in previous.values()
        )
        return refs

    def _dedupe_key(self, *parts: str) -> str:
        return hmac.new(
            self._key(),
            "\x1f".join(parts).encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _purchase_dedupe_key(order_id: str, product_id: str) -> str:
        return hashlib.sha256(f"purchase\x1f{order_id}\x1f{product_id}".encode()).hexdigest()

    async def _response(
        self,
        *,
        user_id: str,
        strategy: RecommendationStrategy,
        products: list[ProductResponse],
        model_version: str,
    ) -> RecommendationResponse:
        now = utc_now()
        recommendation_id = uuid4().hex
        products = list({product.id: product for product in products}.values())
        await self.db.recommendation_slates.insert_one(
            {
                "_id": recommendation_id,
                "schemaVersion": 1,
                "userRef": self._user_ref(user_id),
                "userRefKeyVersion": self.settings.recommendation_user_ref_key_version,
                "placement": DEFAULT_PLACEMENT,
                "strategy": strategy.value,
                "modelVersion": model_version,
                "items": [
                    {"productId": product.id, "rank": rank}
                    for rank, product in enumerate(products, start=1)
                ],
                "createdAt": now,
                "expiresAt": now
                + timedelta(days=self.settings.recommendation_slate_retention_days),
            }
        )
        if self.metrics:
            self.metrics.record_recommendations_served(strategy.value)
        return RecommendationResponse(
            recommendation_id=recommendation_id,
            strategy=strategy,
            products=products,
        )

    async def recommend(self, *, user_id: str, limit: int) -> RecommendationResponse:
        started = perf_counter()
        strategy: RecommendationStrategy | None = None
        selected_products: list[ProductResponse] = []
        model_version = BASELINE_MODEL_VERSION

        if (
            self.settings.recommender_enabled
            and self.settings.recommender_mode == "external_first"
        ):
            external_ids = await self._external_product_ids(user_id=user_id, limit=limit)
            selected_products = await self.products.get_available_by_ids(external_ids)
            if selected_products:
                strategy = RecommendationStrategy.EXTERNAL
                model_version = "external"

        if strategy is None and self.runtime is not None:
            local = await self.runtime.recommend(
                user_id=user_id,
                user_ref=self._user_ref(user_id),
                limit=limit,
            )
            if local is not None:
                selected_products = await self.products.get_available_by_ids(
                    list(local.product_ids)
                )
                if selected_products:
                    strategy = RecommendationStrategy(local.strategy)
                    model_version = local.model_version

        if (
            strategy is None
            and self.settings.recommender_enabled
            and self.settings.recommender_mode == "external_fallback"
        ):
            external_ids = await self._external_product_ids(user_id=user_id, limit=limit)
            selected_products = await self.products.get_available_by_ids(external_ids)
            if selected_products:
                strategy = RecommendationStrategy.EXTERNAL
                model_version = "external"
                if self.metrics:
                    self.metrics.record_recommendation_fallback("external_after_local")

        if strategy is None:
            recent = await self.products.list_recent_available(limit=limit)
            selected_products = recent.products
            strategy = RecommendationStrategy.RECENT
            model_version = "recent"
            if self.metrics:
                self.metrics.record_recommendation_fallback("recent_catalog")

        response = await self._response(
            user_id=user_id,
            strategy=strategy,
            products=selected_products[:limit],
            model_version=model_version,
        )
        if self.metrics:
            self.metrics.observe_recommendation(
                strategy=strategy.value,
                duration_seconds=perf_counter() - started,
            )
        return response

    async def record_client_event(
        self,
        *,
        user_id: str,
        payload: RecommendationEventCreate,
    ) -> bool:
        now = utc_now()
        user_ref = self._user_ref(user_id)
        slate = await self.db.recommendation_slates.find_one(
            {
                "_id": payload.recommendation_id,
                "userRef": user_ref,
                "expiresAt": {"$gt": now},
                "items": {"$elemMatch": {"productId": payload.product_id}},
            }
        )
        if slate is None:
            # Keep cross-user, expired, and arbitrary-product failures indistinguishable.
            raise NotFoundError("Recommendation slate not found")

        products = await self.products.get_available_by_ids([payload.product_id])
        if not products:
            raise NotFoundError("Product not found")

        item = next(
            (
                value
                for value in slate.get("items", [])
                if isinstance(value, dict) and value.get("productId") == payload.product_id
            ),
            None,
        )
        if item is None or not isinstance(item.get("rank"), int):
            raise NotFoundError("Recommendation slate not found")

        event_type = RecommendationEventType(payload.event_type.value)
        dedupe_key = self._dedupe_key(
            "engagement",
            self.settings.recommendation_user_ref_key_version,
            user_ref,
            payload.recommendation_id,
            payload.product_id,
            event_type.value,
        )
        if await self.db.recommendation_events.find_one({"dedupeKey": dedupe_key}):
            return False

        counter_id = self._daily_counter_id(
            user_ref=user_ref,
            product_id=payload.product_id,
            event_type=event_type,
            created_at=now,
        )
        await self._reserve_daily_cap(
            counter_id=counter_id,
            event_type=event_type,
            user_ref=user_ref,
            product_id=payload.product_id,
            now=now,
        )
        try:
            created = await self._insert_event(
                {
                    "schemaVersion": 2,
                    "dedupeKey": dedupe_key,
                    "userRef": user_ref,
                    "userRefKeyVersion": self.settings.recommendation_user_ref_key_version,
                    "eventType": event_type.value,
                    "productId": payload.product_id,
                    "recommendationId": payload.recommendation_id,
                    "rank": item["rank"],
                    "placement": slate.get("placement", DEFAULT_PLACEMENT),
                    "strategy": slate.get("strategy", RecommendationStrategy.RECENT.value),
                    "modelVersion": slate.get("modelVersion", BASELINE_MODEL_VERSION),
                    "source": "served_slate",
                    "quantity": 1,
                    "createdAt": now,
                }
            )
        except Exception:
            await self._release_daily_cap(counter_id)
            raise
        if not created:
            await self._release_daily_cap(counter_id)
        return created

    async def purge_user_data(self, *, user_id: str) -> None:
        """Idempotently remove raw recommendation data for the authenticated user."""
        user_refs = self._all_user_refs(user_id)
        if self.runtime is not None:
            for user_ref in user_refs:
                await self.runtime.evict_user(user_ref)
        query = {"userRef": {"$in": list(user_refs)}}
        await self.db.recommendation_slates.delete_many(query)
        await self.db.recommendation_events.delete_many(query)
        await self.db.recommendation_event_counters.delete_many(query)

    async def record_purchase(self, order: OrderResponse) -> None:
        if order.status is not OrderStatus.COMPLETED:
            return

        quantities: defaultdict[str, int] = defaultdict(int)
        for item in order.items:
            if item.product_id is not None:
                quantities[item.product_id] += item.quantity

        user_ref = self._user_ref(order.user_id)
        created_at = order.completed_at or order.updated_at
        for product_id, quantity in quantities.items():
            attribution = await self.db.recommendation_events.find_one(
                {
                    "userRef": user_ref,
                    "productId": product_id,
                    "eventType": RecommendationEventType.ADD_TO_CART.value,
                    "createdAt": {
                        "$gte": created_at
                        - timedelta(days=self.settings.recommendation_slate_retention_days),
                        "$lte": created_at,
                    },
                },
                sort=[("createdAt", -1)],
            )
            event: dict[str, Any] = {
                "schemaVersion": 2,
                "dedupeKey": self._purchase_dedupe_key(order.id, product_id),
                "userRef": user_ref,
                "userRefKeyVersion": self.settings.recommendation_user_ref_key_version,
                "eventType": RecommendationEventType.PURCHASE.value,
                "productId": product_id,
                "recommendationId": None,
                "rank": None,
                "placement": None,
                "strategy": None,
                "modelVersion": None,
                "source": "completed_order",
                "quantity": quantity,
                "orderId": order.id,
                "createdAt": created_at,
            }
            if attribution is not None:
                for field in (
                    "recommendationId",
                    "rank",
                    "placement",
                    "strategy",
                    "modelVersion",
                ):
                    event[field] = attribution.get(field)
            await self._insert_event(event)

    async def _insert_event(self, event: dict[str, Any]) -> bool:
        try:
            await self.db.recommendation_events.insert_one(event)
        except DuplicateKeyError:
            return False
        if self.metrics:
            self.metrics.record_recommendation_event(str(event["eventType"]))
        return True

    def _event_cap(self, event_type: RecommendationEventType) -> int:
        return {
            RecommendationEventType.IMPRESSION: (
                self.settings.recommendation_daily_impression_cap
            ),
            RecommendationEventType.CLICK: self.settings.recommendation_daily_click_cap,
            RecommendationEventType.ADD_TO_CART: (
                self.settings.recommendation_daily_add_to_cart_cap
            ),
        }[event_type]

    def _daily_counter_id(
        self,
        *,
        user_ref: str,
        product_id: str,
        event_type: RecommendationEventType,
        created_at: datetime,
    ) -> str:
        day = created_at.astimezone(UTC).date().isoformat()
        return self._dedupe_key("daily-cap", user_ref, product_id, event_type.value, day)

    async def _reserve_daily_cap(
        self,
        *,
        counter_id: str,
        event_type: RecommendationEventType,
        user_ref: str,
        product_id: str,
        now: datetime,
    ) -> None:
        next_day = datetime.combine(
            now.astimezone(UTC).date() + timedelta(days=1),
            time.min,
            tzinfo=UTC,
        )
        try:
            result = await self.db.recommendation_event_counters.find_one_and_update(
                {
                    "_id": counter_id,
                    "$or": [
                        {"count": {"$lt": self._event_cap(event_type)}},
                        {"count": {"$exists": False}},
                    ],
                },
                {
                    "$inc": {"count": 1},
                    "$setOnInsert": {
                        "userRef": user_ref,
                        "productId": product_id,
                        "eventType": event_type.value,
                        "day": now.astimezone(UTC).date().isoformat(),
                        "expiresAt": next_day + timedelta(days=1),
                    },
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as exc:
            raise ConflictError("Daily recommendation event limit reached") from exc
        if result is None:
            raise ConflictError("Daily recommendation event limit reached")

    async def _release_daily_cap(self, counter_id: str) -> None:
        await self.db.recommendation_event_counters.update_one(
            {"_id": counter_id, "count": {"$gt": 0}},
            {"$inc": {"count": -1}},
        )

    async def _external_product_ids(self, *, user_id: str, limit: int) -> list[str]:
        if self.settings.recommender_url is None:
            return []
        try:
            response = await self.http_client.post(
                str(self.settings.recommender_url),
                json={"user_ref": self._user_ref(user_id), "limit": limit},
                timeout=self.settings.recommender_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning(
                "external_recommender_failed",
                extra={"error_type": type(exc).__name__},
            )
            return []
        product_ids = payload.get("product_ids") if isinstance(payload, dict) else None
        if not isinstance(product_ids, list):
            return []
        return list(dict.fromkeys(value for value in product_ids if isinstance(value, str)))[
            : limit * 3
        ]
