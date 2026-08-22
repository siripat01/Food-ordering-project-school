from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Self
from uuid import uuid4

from bson import BSON, ObjectId
from bson.errors import InvalidDocument
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, InsertOne, ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.config import Settings
from app.services.recommendation_models import (
    ActivationDecision,
    ActivationQualityGate,
    CPUModelBuildResult,
    ModelBuildConfig,
    ModelBuildLimitError,
    RecommendationInteraction,
    TemporalEvaluationResult,
    build_cpu_recommendation_model,
    decide_model_activation,
    evaluate_temporal_split,
    load_cpu_recommendation_model,
)


def _utc(value: datetime | None, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class MongoBuildLease(AbstractAsyncContextManager["MongoBuildLease"]):
    """A bounded lease preventing concurrent artifact writers/activators."""

    def __init__(
        self,
        collection: Any,
        *,
        owner: str,
        lease_seconds: int,
    ) -> None:
        self.collection = collection
        self.owner = owner
        self.lease_seconds = lease_seconds
        self.acquired = False

    async def __aenter__(self) -> Self:
        now = datetime.now(UTC)
        try:
            document = await self.collection.find_one_and_update(
                {
                    "_id": "cpu-recommendation-builder",
                    "$or": [
                        {"expiresAt": {"$lte": now}},
                        {"owner": self.owner},
                    ],
                },
                {
                    "$set": {
                        "owner": self.owner,
                        "acquiredAt": now,
                        "expiresAt": now + timedelta(seconds=self.lease_seconds),
                    }
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as exc:
            raise RuntimeError("Another recommendation model build holds the lease") from exc
        if document is None or document.get("owner") != self.owner:
            raise RuntimeError("Another recommendation model build holds the lease")
        self.acquired = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.acquired:
            await self.collection.delete_one(
                {"_id": "cpu-recommendation-builder", "owner": self.owner}
            )
            self.acquired = False


async def load_catalog_product_ids(
    products: Any,
    *,
    max_products: int,
) -> set[str]:
    cursor = products.find(
        {"status": {"$in": ["available", "avalible", "active"]}},
        {"_id": 1},
    ).limit(max_products + 1)
    documents = await cursor.to_list()
    if len(documents) > max_products:
        raise ModelBuildLimitError(f"Catalog exceeds max_catalog_products={max_products}")
    return {str(document["_id"]) for document in documents}


async def load_recent_product_ids(
    products: Any,
    *,
    max_products: int,
    created_before: datetime | None = None,
) -> list[str]:
    query: dict[str, object] = {"status": {"$in": ["available", "avalible", "active"]}}
    if created_before is not None:
        query["$or"] = [
            {"createdAt": {"$lte": created_before}},
            {"createdAt": {"$exists": False}},
        ]
    cursor = (
        products.find(
            query,
            {"_id": 1},
        )
        .sort("createdAt", DESCENDING)
        .limit(max_products + 1)
    )
    documents = await cursor.to_list()
    if len(documents) > max_products:
        raise ModelBuildLimitError(f"Catalog exceeds max_catalog_products={max_products}")
    return [str(document["_id"]) for document in documents]


async def iter_completed_order_interactions(
    orders: Any,
    *,
    start: datetime,
    end: datetime,
    batch_size: int,
    user_ref_key: bytes,
) -> AsyncIterator[RecommendationInteraction]:
    query = {
        "status": "completed",
        "$or": [
            {"completedAt": {"$gte": start, "$lte": end}},
            {
                "completedAt": {"$exists": False},
                "updatedAt": {"$gte": start, "$lte": end},
            },
        ],
    }
    cursor = (
        orders.find(
            query,
            {
                "userId": 1,
                "items.productId": 1,
                "items.quantity": 1,
                "completedAt": 1,
                "updatedAt": 1,
            },
        )
        .sort("completedAt", ASCENDING)
        .batch_size(batch_size)
    )
    async for order in cursor:
        order_id = str(order["_id"])
        user_id = order.get("userId")
        if not isinstance(user_id, str) or not user_id:
            continue
        occurred_at = _utc(
            order.get("completedAt") or order.get("updatedAt"),
            end,
        )
        items = order.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            product_id = item.get("productId")
            if not isinstance(product_id, str) or not product_id:
                continue
            raw_quantity = item.get("quantity", 1)
            quantity = raw_quantity if isinstance(raw_quantity, int) else 1
            user_ref = hmac.new(user_ref_key, user_id.encode(), hashlib.sha256).hexdigest()
            yield RecommendationInteraction(
                user_ref=user_ref,
                product_id=product_id,
                event_type="purchase",
                occurred_at=occurred_at,
                quantity=max(1, quantity),
                order_id=order_id,
            )


async def iter_engagement_interactions(
    events: Any,
    *,
    start: datetime,
    end: datetime,
    batch_size: int,
) -> AsyncIterator[RecommendationInteraction]:
    cursor = (
        events.find(
            {
                "eventType": {"$in": ["impression", "click", "add_to_cart"]},
                "createdAt": {"$gte": start, "$lte": end},
            },
            {
                "userRef": 1,
                "productId": 1,
                "eventType": 1,
                "quantity": 1,
                "createdAt": 1,
            },
        )
        .sort("createdAt", ASCENDING)
        .batch_size(batch_size)
    )
    async for event in cursor:
        user_ref = event.get("userRef")
        product_id = event.get("productId")
        event_type = event.get("eventType")
        occurred_at = event.get("createdAt")
        if not all(isinstance(value, str) and value for value in (user_ref, product_id)):
            continue
        if not isinstance(event_type, str) or not isinstance(occurred_at, datetime):
            continue
        yield RecommendationInteraction(
            user_ref=user_ref,
            product_id=product_id,
            event_type=event_type,
            occurred_at=_utc(occurred_at, end),
            quantity=1,
        )


async def load_bounded_interactions(
    orders: Any,
    events: Any,
    *,
    start: datetime,
    end: datetime,
    max_interactions: int,
    batch_size: int,
    user_ref_key: bytes,
) -> list[RecommendationInteraction]:
    interactions: list[RecommendationInteraction] = []

    async def collect(source: AsyncIterator[RecommendationInteraction]) -> None:
        async for interaction in source:
            if len(interactions) >= max_interactions:
                raise ModelBuildLimitError(
                    f"Source exceeds max_interactions={max_interactions}"
                )
            interactions.append(interaction)

    await collect(
        iter_completed_order_interactions(
            orders,
            start=start,
            end=end,
            batch_size=batch_size,
            user_ref_key=user_ref_key,
        )
    )
    await collect(
        iter_engagement_interactions(
            events,
            start=start,
            end=end,
            batch_size=batch_size,
        )
    )
    return interactions


async def write_model(
    database: Any,
    *,
    build: CPUModelBuildResult,
    offline_metrics: dict[str, object],
    activation_decision: ActivationDecision,
    activate: bool,
    max_artifact_bytes: int,
) -> str | None:
    versions = database["recommendation_model_versions"]
    artifacts = database["recommendation_artifacts"]
    version_document = build.version_document(
        status="building", offline_metrics=offline_metrics
    )
    version_document["activationEligibility"] = {
        **activation_decision.to_document(),
        "evaluatedAt": datetime.now(UTC),
    }
    version_document["hasBeenActive"] = False
    await versions.insert_one(version_document)
    try:
        artifact_bytes = serialized_artifact_bytes(build)
        if artifact_bytes > max_artifact_bytes:
            raise ModelBuildLimitError("Model artifacts exceed the runtime byte limit")
        operations = [InsertOne(artifact.to_document()) for artifact in build.artifacts]
        for offset in range(0, len(operations), 500):
            await artifacts.bulk_write(operations[offset : offset + 500], ordered=True)
        artifact_count = await artifacts.count_documents({"modelVersion": build.version})
        if artifact_count != len(build.artifacts):
            raise RuntimeError("Stored artifact count does not match model metadata")
        update = await versions.update_one(
            {"_id": build.version, "status": "building"},
            {"$set": {"status": "ready"}},
        )
        if update.matched_count != 1:
            raise RuntimeError("Model version could not transition to ready")
    except Exception as exc:
        await artifacts.delete_many({"modelVersion": build.version})
        await versions.update_one(
            {"_id": build.version, "status": "building"},
            {
                "$set": {
                    "status": "failed",
                    "failedAt": datetime.now(UTC),
                    "failureType": type(exc).__name__,
                }
            },
        )
        raise
    if not activate:
        return None
    if not activation_decision.approved:
        raise RuntimeError("A model that failed its quality gate cannot be activated")
    if not build.artifacts:
        raise RuntimeError("A model without artifacts cannot be activated")
    return await set_active_model(
        database,
        version=build.version,
        algorithm=build.algorithm,
    )


def serialized_artifact_bytes(build: CPUModelBuildResult) -> int:
    # PyMongo adds an ObjectId to each inserted artifact. Include that field so the
    # build-time gate matches the runtime BSON documents instead of undercounting.
    return sum(
        len(BSON.encode({"_id": ObjectId(), **artifact.to_document()}))
        for artifact in build.artifacts
    )


async def set_active_model(
    database: Any,
    *,
    version: str,
    algorithm: str,
) -> str | None:
    state = database["recommendation_model_state"]
    current = await state.find_one({"_id": "active"})
    previous = current.get("modelVersion") if current else None
    if isinstance(previous, str) and previous != version:
        previous_version = previous
    else:
        existing_previous = current.get("previousModelVersion") if current else None
        previous_version = existing_previous if isinstance(existing_previous, str) else None
    version_update = await database["recommendation_model_versions"].update_one(
        {"_id": version, "status": "ready"},
        {
            "$set": {
                "hasBeenActive": True,
                "lastActivatedAt": datetime.now(UTC),
            }
        },
    )
    if version_update.matched_count != 1:
        raise RuntimeError("Active model version is no longer ready")
    await state.update_one(
        {"_id": "active"},
        {
            "$set": {
                "modelVersion": version,
                "previousModelVersion": previous_version,
                "algorithm": algorithm,
                "activatedAt": datetime.now(UTC),
            }
        },
        upsert=True,
    )
    return previous_version


async def ensure_model_indexes(database: Any) -> None:
    await database["recommendation_artifacts"].create_index(
        [("modelVersion", ASCENDING), ("productId", ASCENDING)],
        unique=True,
        name="uniq_recommendation_artifact_model_product",
    )
    await database["recommendation_artifacts"].create_index(
        [("modelVersion", ASCENDING)],
        name="recommendation_artifact_model",
    )
    await database["recommendation_model_versions"].create_index(
        [("status", ASCENDING), ("builtAt", DESCENDING)],
        name="recommendation_model_status_built",
    )
    await database["recommendation_model_versions"].create_index(
        [("builtAt", DESCENDING)],
        name="recommendation_model_built",
    )
    await database["orders"].create_index(
        [("status", ASCENDING), ("completedAt", DESCENDING)],
        name="orders_completed_training",
    )
    await database["recommendation_model_locks"].create_index(
        [("expiresAt", ASCENDING)],
        expireAfterSeconds=0,
        name="ttl_recommendation_model_lock",
    )


async def activate_existing_model(
    database: Any,
    version: str,
    *,
    max_products: int,
    max_bytes: int,
) -> dict[str, object]:
    document = await database["recommendation_model_versions"].find_one(
        {"_id": version, "status": "ready"}
    )
    if document is None:
        raise RuntimeError("Rollback model is not ready or has no artifacts")
    state = await database["recommendation_model_state"].find_one({"_id": "active"})
    protected_versions = {
        value
        for value in (
            state.get("modelVersion") if state else None,
            state.get("previousModelVersion") if state else None,
        )
        if isinstance(value, str)
    }
    eligibility = document.get("activationEligibility")
    eligible = isinstance(eligibility, dict) and eligibility.get("approved") is True
    has_been_active = document.get("hasBeenActive") is True
    if not eligible and not has_been_active and version not in protected_versions:
        raise RuntimeError("Model failed its activation quality gate")
    cursor = (
        database["recommendation_artifacts"]
        .find({"modelVersion": version})
        .limit(max_products + 1)
    )
    artifact_documents = await cursor.to_list()
    if not artifact_documents or len(artifact_documents) > max_products:
        raise RuntimeError("Rollback model artifact count is invalid")
    try:
        serialized_bytes = sum(len(BSON.encode(document)) for document in artifact_documents)
    except (InvalidDocument, TypeError, ValueError) as exc:
        raise RuntimeError("Rollback model artifacts failed BSON validation") from exc
    if serialized_bytes > max_bytes:
        raise RuntimeError("Rollback model artifacts exceed the runtime byte limit")
    try:
        loaded = load_cpu_recommendation_model(
            document,
            artifact_documents,
            max_serialized_bytes=max_bytes,
        )
    except (ValueError, TypeError, ModelBuildLimitError) as exc:
        raise RuntimeError("Rollback model artifacts failed validation") from exc
    previous = await set_active_model(
        database,
        version=loaded.version,
        algorithm=loaded.algorithm,
    )
    return {
        "mode": "rollback",
        "modelVersion": version,
        "previousModelVersion": previous,
    }


async def cleanup_old_models(
    database: Any,
    *,
    retain_versions: int = 2,
    retain_days: int = 30,
) -> dict[str, int]:
    if retain_versions < 1 or retain_days < 1:
        raise ValueError("Model retention values must be positive")
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=retain_days)
    cursor = (
        database["recommendation_model_versions"]
        .find(
            {},
            {"_id": 1, "builtAt": 1, "status": 1},
        )
        .sort("builtAt", DESCENDING)
    )
    documents = await cursor.to_list()
    state = await database["recommendation_model_state"].find_one({"_id": "active"})
    protected = {
        value
        for value in (
            state.get("modelVersion") if state else None,
            state.get("previousModelVersion") if state else None,
        )
        if isinstance(value, str)
    }
    ready_documents = [document for document in documents if document.get("status") == "ready"]
    keep = {str(document["_id"]) for document in ready_documents[:retain_versions]} | protected
    versions_deleted = 0
    artifacts_deleted = 0
    for document in documents:
        version = str(document["_id"])
        built_at = document.get("builtAt")
        if version in keep or not isinstance(built_at, datetime):
            continue
        if _utc(built_at, now) >= cutoff:
            continue
        artifact_result = await database["recommendation_artifacts"].delete_many(
            {"modelVersion": version}
        )
        version_result = await database["recommendation_model_versions"].delete_one(
            {"_id": version}
        )
        artifacts_deleted += int(artifact_result.deleted_count)
        versions_deleted += int(version_result.deleted_count)
    return {
        "versionsDeleted": versions_deleted,
        "artifactsDeleted": artifacts_deleted,
    }


async def incumbent_metrics(database: Any) -> dict[str, object] | None:
    state = await database["recommendation_model_state"].find_one({"_id": "active"})
    if not state or not isinstance(state.get("modelVersion"), str):
        return None
    version = await database["recommendation_model_versions"].find_one(
        {"_id": state["modelVersion"]},
        {"offlineMetrics": 1},
    )
    if not version or not isinstance(version.get("offlineMetrics"), dict):
        return None
    return version["offlineMetrics"]


def decide_build_activation(
    candidate: TemporalEvaluationResult,
    *,
    trending_baseline: TemporalEvaluationResult,
    incumbent: dict[str, object] | None,
    gate: ActivationQualityGate,
    artifact_bytes: int,
    max_artifact_bytes: int,
) -> ActivationDecision:
    decision = decide_model_activation(
        candidate,
        incumbent_metrics=incumbent,
        gate=gate,
    )
    reasons = list(decision.reasons)
    candidate_ndcg = candidate.metrics.get("ndcgAt10", 0.0)
    baseline_ndcg = trending_baseline.metrics.get("ndcgAt10", 0.0)
    baseline_floor = baseline_ndcg - gate.max_ndcg_at_10_regression
    if candidate_ndcg < baseline_floor:
        reasons.append(
            f"ndcgAt10 {candidate_ndcg:.4f} < trending baseline floor {baseline_floor:.4f}"
        )
    if artifact_bytes > max_artifact_bytes:
        reasons.append(f"artifact bytes {artifact_bytes} > runtime limit {max_artifact_bytes}")
    return ActivationDecision(approved=not reasons, reasons=tuple(reasons))


def offline_metrics_document(
    candidate: TemporalEvaluationResult,
    trending_baseline: TemporalEvaluationResult,
    recent_baseline: TemporalEvaluationResult,
) -> dict[str, object]:
    document = candidate.to_document()
    document["trendingBaseline"] = trending_baseline.to_document()
    document["recentBaseline"] = recent_baseline.to_document()
    item_item_minus_trending = {
        metric: candidate.metrics[metric] - trending_baseline.metrics[metric]
        for metric in ("recallAt5", "ndcgAt5", "recallAt10", "ndcgAt10")
    }
    item_item_minus_trending["catalogCoverageDelta"] = (
        candidate.catalog_coverage - trending_baseline.catalog_coverage
    )
    item_item_minus_trending["popularityShareDelta"] = (
        candidate.popularity_share - trending_baseline.popularity_share
    )
    item_item_minus_recent = {
        metric: candidate.metrics[metric] - recent_baseline.metrics[metric]
        for metric in ("recallAt5", "ndcgAt5", "recallAt10", "ndcgAt10")
    }
    item_item_minus_recent["catalogCoverageDelta"] = (
        candidate.catalog_coverage - recent_baseline.catalog_coverage
    )
    item_item_minus_recent["popularityShareDelta"] = (
        candidate.popularity_share - recent_baseline.popularity_share
    )
    document["comparison"] = {
        "itemItemMinusTrending": item_item_minus_trending,
        "itemItemMinusRecent": item_item_minus_recent,
    }
    return document


async def run(args: argparse.Namespace) -> dict[str, object]:
    settings = Settings()
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        settings.mongodb_uri.get_secret_value(),
        serverSelectionTimeoutMS=5_000,
        tz_aware=True,
        appname="food-ordering-recommendation-builder",
    )
    now = datetime.now(UTC)
    evaluation_cutoff = now - timedelta(days=args.test_days)
    config = ModelBuildConfig(
        training_window_days=args.window_days,
        trending_half_life_days=args.half_life_days,
        max_interactions=args.max_interactions,
        max_catalog_products=args.max_catalog_products,
        max_users=args.max_users,
        max_history_per_user=args.max_history,
        max_basket_items=args.max_basket_items,
        max_pair_entries=args.max_pair_entries,
        min_pair_support=args.min_support,
        top_neighbors=args.top_neighbors,
        max_candidates=args.max_candidates,
        same_user_history_pair_weight=args.same_user_history_weight,
    )
    config.validate()
    database = client[settings.mongodb_orders_database]
    products = client[settings.mongodb_products_database]["products"]
    owner = uuid4().hex
    lease: AbstractAsyncContextManager[Any]
    lease = (
        MongoBuildLease(
            database["recommendation_model_locks"],
            owner=owner,
            lease_seconds=args.lease_seconds,
        )
        if args.write
        else _NoopAsyncContext()
    )
    try:
        await client.admin.command("ping")
        async with lease:
            if args.write:
                await ensure_model_indexes(database)
            if args.rollback_version:
                return await activate_existing_model(
                    database,
                    args.rollback_version,
                    max_products=args.max_catalog_products,
                    max_bytes=settings.recommendation_model_max_bytes,
                )
            catalog = await load_catalog_product_ids(
                products,
                max_products=config.max_catalog_products,
            )
            recent_product_ids = await load_recent_product_ids(
                products,
                max_products=config.max_catalog_products,
                created_before=evaluation_cutoff,
            )
            source_start = evaluation_cutoff - timedelta(days=config.training_window_days)
            interactions = await load_bounded_interactions(
                database["orders"],
                database["recommendation_events"],
                start=source_start,
                end=now,
                max_interactions=config.max_interactions,
                batch_size=args.batch_size,
                user_ref_key=(
                    settings.recommendation_user_ref_secret.get_secret_value().encode()
                ),
            )
            evaluation = evaluate_temporal_split(
                interactions,
                catalog_product_ids=catalog,
                cutoff=evaluation_cutoff,
                config=config,
                algorithm="item_item",
            )
            trending_evaluation = evaluate_temporal_split(
                interactions,
                catalog_product_ids=catalog,
                cutoff=evaluation_cutoff,
                config=config,
                algorithm="trending",
            )
            recent_evaluation = evaluate_temporal_split(
                interactions,
                catalog_product_ids=catalog,
                cutoff=evaluation_cutoff,
                config=config,
                algorithm="recent",
                recent_product_ids=recent_product_ids,
            )
            build = build_cpu_recommendation_model(
                interactions,
                catalog_product_ids=catalog,
                training_cutoff=now,
                config=config,
            )
            artifact_bytes = serialized_artifact_bytes(build)
            current_metrics = await incumbent_metrics(database)
            quality_gate = ActivationQualityGate(
                min_evaluation_users=args.min_evaluation_users,
                min_catalog_coverage=args.min_catalog_coverage,
                max_ndcg_at_10_regression=args.max_ndcg_regression,
            )
            decision = decide_build_activation(
                evaluation,
                trending_baseline=trending_evaluation,
                incumbent=current_metrics,
                gate=quality_gate,
                artifact_bytes=artifact_bytes,
                max_artifact_bytes=settings.recommendation_model_max_bytes,
            )
            should_activate = bool(args.activate and decision.approved)
            metrics = offline_metrics_document(
                evaluation,
                trending_evaluation,
                recent_evaluation,
            )
            metrics["artifactBytes"] = artifact_bytes
            retention = {"versionsDeleted": 0, "artifactsDeleted": 0}
            if args.write:
                await write_model(
                    database,
                    build=build,
                    offline_metrics=metrics,
                    activation_decision=decision,
                    activate=should_activate,
                    max_artifact_bytes=settings.recommendation_model_max_bytes,
                )
                if should_activate:
                    retention = await cleanup_old_models(
                        database,
                        retain_versions=args.retain_versions,
                        retain_days=args.retain_days,
                    )
            return {
                "mode": "write" if args.write else "dry-run",
                "modelVersion": build.version,
                "artifactCount": len(build.artifacts),
                "buildStats": build.stats.to_document(),
                "offlineMetrics": metrics,
                "retention": retention,
                "activation": {
                    **decision.to_document(),
                    "requested": bool(args.activate),
                    "performed": should_activate,
                },
            }
    finally:
        await client.close()


class _NoopAsyncContext(AbstractAsyncContextManager[None]):
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Build bounded CPU-only trending and item-item recommendation artifacts."
    )
    command.add_argument("--write", action="store_true")
    command.add_argument("--activate", action="store_true")
    command.add_argument("--rollback-version")
    command.add_argument("--window-days", type=int, default=180)
    command.add_argument("--test-days", type=int, default=14)
    command.add_argument("--half-life-days", type=float, default=14.0)
    command.add_argument("--max-interactions", type=int, default=250_000)
    command.add_argument("--max-catalog-products", type=int, default=10_000)
    command.add_argument("--max-users", type=int, default=100_000)
    command.add_argument("--max-history", type=int, default=20)
    command.add_argument("--max-basket-items", type=int, default=20)
    command.add_argument("--max-pair-entries", type=int, default=500_000)
    command.add_argument("--min-support", type=int, default=3)
    command.add_argument("--top-neighbors", type=int, default=50)
    command.add_argument("--max-candidates", type=int, default=200)
    command.add_argument("--same-user-history-weight", type=float, default=0.0)
    command.add_argument("--batch-size", type=int, default=500)
    command.add_argument("--lease-seconds", type=int, default=900)
    command.add_argument("--retain-versions", type=int, default=2)
    command.add_argument("--retain-days", type=int, default=30)
    command.add_argument("--min-evaluation-users", type=int, default=5)
    command.add_argument("--min-catalog-coverage", type=float, default=0.05)
    command.add_argument("--max-ndcg-regression", type=float, default=0.02)
    return command


def main() -> None:
    command = parser()
    args = command.parse_args()
    positive = (
        args.window_days,
        args.test_days,
        args.max_interactions,
        args.max_catalog_products,
        args.max_users,
        args.max_history,
        args.max_basket_items,
        args.max_pair_entries,
        args.min_support,
        args.top_neighbors,
        args.max_candidates,
        args.batch_size,
        args.lease_seconds,
        args.retain_versions,
        args.retain_days,
    )
    if any(value <= 0 for value in positive):
        command.error("All window, limit, batch and lease arguments must be positive")
    if (args.activate or args.rollback_version) and not args.write:
        command.error("--activate and --rollback-version require --write")
    if args.activate and args.rollback_version:
        command.error("--activate and --rollback-version are mutually exclusive")
    result = asyncio.run(run(args))
    print(json.dumps(result, default=str, separators=(",", ":")))


if __name__ == "__main__":
    main()
