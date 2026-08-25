from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass

from bson import BSON
from pymongo.errors import PyMongoError
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.observability import ApplicationMetrics
from app.db.mongodb import MongoDatabase
from app.domain.common import utc_now
from app.services.recommendation_models import (
    CPUModelBuildResult,
    ModelBuildLimitError,
    RecommendationAlgorithm,
    load_cpu_recommendation_model,
    rank_products,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RuntimeRecommendation:
    product_ids: tuple[str, ...]
    strategy: RecommendationAlgorithm
    model_version: str


class RecommendationModelRuntime:
    """Loads immutable CPU artifacts and serves bounded personalized rankings."""

    def __init__(
        self,
        *,
        db: MongoDatabase,
        settings: Settings,
        metrics: ApplicationMetrics | None = None,
        redis: Redis,
    ) -> None:
        self.db = db
        self.settings = settings
        self.metrics = metrics
        self.redis = redis
        self._model: CPUModelBuildResult | None = None
        self._last_model_poll = 0.0
        self._has_polled = False
        self._model_lock = asyncio.Lock()

    async def recommend(
        self,
        *,
        user_id: str,
        user_ref: str,
        limit: int,
    ) -> RuntimeRecommendation | None:
        model = await self._active_model()
        if model is None:
            return None
        try:
            profile = await self._recent_profile(user_id)
        except PyMongoError:
            logger.warning("recommendation_profile_store_unavailable")
            profile = ()
        algorithm = self._algorithm(user_ref=user_ref, has_profile=bool(profile))
        key = (user_ref, model.version, algorithm, limit)
        cached = await self._cached_result(key)
        if cached is not None:
            if self.metrics:
                self.metrics.record_recommendation_cache(cache="result", outcome="hit")
            return cached
        if self.metrics:
            self.metrics.record_recommendation_cache(cache="result", outcome="miss")
        product_ids = tuple(
            rank_products(
                model,
                profile_product_ids=profile,
                limit=min(limit, 20),
                algorithm=algorithm,
            )
        )
        if not product_ids:
            return None
        result = RuntimeRecommendation(
            product_ids=product_ids,
            strategy=algorithm,
            model_version=model.version,
        )
        await self.redis.set(
            self._result_key(key),
            json.dumps(
                {
                    "productIds": product_ids,
                    "strategy": algorithm,
                    "modelVersion": model.version,
                },
                separators=(",", ":"),
            ),
            ex=self.settings.recommendation_result_cache_ttl_seconds,
        )
        return result

    def _algorithm(self, *, user_ref: str, has_profile: bool) -> RecommendationAlgorithm:
        if not has_profile or self.settings.recommendation_item_item_rollout_percent <= 0:
            return "trending"
        bucket = int.from_bytes(hashlib.sha256(user_ref.encode()).digest()[:4], "big") % 100
        if bucket < self.settings.recommendation_item_item_rollout_percent:
            return "item_item"
        return "trending"

    async def evict_user(self, user_ref: str) -> None:
        """Remove all short-lived personalized results for one pseudonymous user."""
        pattern = f"recommendation:result:{user_ref}:*"
        keys = [key async for key in self.redis.scan_iter(match=pattern)]
        if keys:
            await self.redis.delete(*keys)

    @staticmethod
    def _result_key(key: tuple[str, str, str, int]) -> str:
        user_ref, version, algorithm, limit = key
        return f"recommendation:result:{user_ref}:{version}:{algorithm}:{limit}"

    async def _cached_result(
        self, key: tuple[str, str, str, int]
    ) -> RuntimeRecommendation | None:
        raw = await self.redis.get(self._result_key(key))
        if raw is None:
            return None
        try:
            value = json.loads(raw)
            product_ids = value["productIds"]
            strategy = value["strategy"]
            model_version = value["modelVersion"]
            if not (
                isinstance(product_ids, list)
                and all(isinstance(product_id, str) for product_id in product_ids)
                and strategy in {"trending", "item_item"}
                and isinstance(model_version, str)
            ):
                raise ValueError
            return RuntimeRecommendation(tuple(product_ids), strategy, model_version)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            await self.redis.delete(self._result_key(key))
            return None

    async def _active_model(self) -> CPUModelBuildResult | None:
        now_monotonic = time.monotonic()
        if (
            self._has_polled
            and now_monotonic - self._last_model_poll
            < self.settings.recommendation_model_poll_seconds
        ):
            if self.metrics:
                self.metrics.record_recommendation_cache(cache="artifact", outcome="hit")
            return self._model
        async with self._model_lock:
            now_monotonic = time.monotonic()
            if (
                self._has_polled
                and now_monotonic - self._last_model_poll
                < self.settings.recommendation_model_poll_seconds
            ):
                return self._model
            self._last_model_poll = now_monotonic
            self._has_polled = True
            try:
                state = await self.db.recommendation_model_state.find_one({"_id": "active"})
                version = state.get("modelVersion") if state else None
                if not isinstance(version, str):
                    self._model = None
                    if self.metrics:
                        self.metrics.record_recommendation_cache(
                            cache="artifact", outcome="miss"
                        )
                    return None
                if self._model is not None and self._model.version == version:
                    return self._model
                version_document = await self.db.recommendation_model_versions.find_one(
                    {"_id": version, "status": "ready"}
                )
                if version_document is None:
                    logger.warning("recommendation_active_model_not_ready")
                    return self._model
                artifact_documents = await self._load_bounded_artifacts(version)
            except (PyMongoError, TypeError, ValueError):
                logger.warning("recommendation_model_store_unavailable")
                return self._model
            except ModelBuildLimitError:
                logger.warning("recommendation_active_model_rejected")
                return self._model
            try:
                loaded = load_cpu_recommendation_model(
                    version_document,
                    artifact_documents,
                    max_serialized_bytes=self.settings.recommendation_model_max_bytes,
                )
            except (ValueError, TypeError, ModelBuildLimitError):
                logger.warning("recommendation_active_model_rejected")
                return self._model
            self._model = loaded
            if self.metrics:
                self.metrics.record_recommendation_cache(cache="artifact", outcome="miss")
                self.metrics.set_recommendation_model_age(
                    (utc_now() - loaded.built_at).total_seconds()
                )
            return loaded

    async def _load_bounded_artifacts(self, version: str) -> list[dict[str, object]]:
        cursor = self.db.recommendation_artifacts.find({"modelVersion": version}).limit(
            self.settings.recommendation_model_max_products + 1
        )
        documents: list[dict[str, object]] = []
        serialized_bytes = 0
        async for document in cursor:
            if len(documents) >= self.settings.recommendation_model_max_products:
                raise ModelBuildLimitError("Stored model exceeds product limit")
            serialized_bytes += len(BSON.encode(document))
            if serialized_bytes > self.settings.recommendation_model_max_bytes:
                raise ModelBuildLimitError("Stored model exceeds serialized byte limit")
            documents.append(document)
        return documents

    async def _recent_profile(self, user_id: str) -> tuple[str, ...]:
        cursor = (
            self.db.orders.find(
                {"userId": user_id, "status": "completed"},
                {"items.productId": 1},
            )
            .sort("completedAt", -1)
            .limit(self.settings.recommendation_profile_order_limit)
        )
        documents = await cursor.to_list()
        product_ids: list[str] = []
        seen: set[str] = set()
        for document in documents:
            items = document.get("items")
            if not isinstance(items, list):
                continue
            for item in items:
                product_id = item.get("productId") if isinstance(item, dict) else None
                if not isinstance(product_id, str) or product_id in seen:
                    continue
                seen.add(product_id)
                product_ids.append(product_id)
                if len(product_ids) >= self.settings.recommendation_profile_product_limit:
                    return tuple(product_ids)
        return tuple(product_ids)
