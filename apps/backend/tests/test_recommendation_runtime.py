from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.recommendation_models import (
    ActivationDecision,
    ActivationQualityGate,
    ModelBuildConfig,
    ModelBuildLimitError,
    RecommendationInteraction,
    build_cpu_recommendation_model,
    evaluate_temporal_split,
)
from app.services.recommendation_runtime import RecommendationModelRuntime
from scripts.build_recommendation_model import (
    activate_existing_model,
    cleanup_old_models,
    decide_build_activation,
    ensure_model_indexes,
    iter_completed_order_interactions,
    offline_metrics_document,
    write_model,
)

NOW = datetime(2026, 8, 22, 12, tzinfo=UTC)


class AsyncListCursor:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents

    def limit(self, _limit: int) -> AsyncListCursor:
        return self

    def sort(self, *_args: object) -> AsyncListCursor:
        return self

    def batch_size(self, _batch_size: int) -> AsyncListCursor:
        return self

    async def to_list(self) -> list[dict[str, object]]:
        return list(self.documents)

    def __aiter__(self):  # type: ignore[no-untyped-def]
        async def iterate():  # type: ignore[no-untyped-def]
            for document in self.documents:
                yield document

        return iterate()


class FakeDatabase:
    def __init__(self, **collections: object) -> None:
        self.collections = collections

    def __getitem__(self, name: str) -> object:
        return self.collections[name]


def cpu_model(version: str = "model-v1"):
    return build_cpu_recommendation_model(
        [
            RecommendationInteraction("u1", "a", "purchase", NOW, order_id="o1"),
            RecommendationInteraction("u1", "b", "purchase", NOW, order_id="o1"),
        ],
        catalog_product_ids={"a", "b"},
        training_cutoff=NOW,
        config=ModelBuildConfig(min_pair_support=1),
        version=version,
    )


def runtime_settings(**updates: object) -> SimpleNamespace:
    values = {
        "recommendation_model_poll_seconds": 30,
        "recommendation_model_max_products": 100,
        "recommendation_model_max_bytes": 1_000_000,
        "recommendation_result_cache_ttl_seconds": 30,
        "recommendation_result_cache_max_entries": 100,
        "recommendation_item_item_rollout_percent": 100,
        "recommendation_profile_order_limit": 20,
        "recommendation_profile_product_limit": 20,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def runtime_database(
    *,
    states: list[dict[str, object] | None],
    versions: list[dict[str, object] | None],
    artifacts: list[list[dict[str, object]]],
) -> SimpleNamespace:
    state_collection = AsyncMock()
    state_collection.find_one.side_effect = states
    version_collection = AsyncMock()
    version_collection.find_one.side_effect = versions
    artifact_collection = MagicMock()
    artifact_collection.find.side_effect = [
        AsyncListCursor(documents) for documents in artifacts
    ]
    orders = MagicMock()
    return SimpleNamespace(
        recommendation_model_state=state_collection,
        recommendation_model_versions=version_collection,
        recommendation_artifacts=artifact_collection,
        orders=orders,
    )


@pytest.mark.asyncio
async def test_order_training_interactions_use_hmac_pseudonyms_not_raw_user_ids() -> None:
    raw_user_id = "raw-database-user-id"
    orders = MagicMock()
    orders.find.return_value = AsyncListCursor(
        [
            {
                "_id": "order-1",
                "userId": raw_user_id,
                "items": [{"productId": "product-1", "quantity": 2}],
                "completedAt": NOW,
            }
        ]
    )

    interactions = [
        interaction
        async for interaction in iter_completed_order_interactions(
            orders,
            start=NOW - timedelta(days=1),
            end=NOW,
            batch_size=100,
            user_ref_key=b"recommendation-test-hmac-key",
        )
    ]

    assert len(interactions) == 1
    assert interactions[0].user_ref != raw_user_id
    assert raw_user_id not in interactions[0].user_ref
    assert len(interactions[0].user_ref) == 64


@pytest.mark.asyncio
async def test_builder_indexes_cover_training_retention_and_runtime_loads() -> None:
    artifacts = AsyncMock()
    versions = AsyncMock()
    locks = AsyncMock()
    orders = AsyncMock()
    database = FakeDatabase(
        recommendation_artifacts=artifacts,
        recommendation_model_versions=versions,
        recommendation_model_locks=locks,
        orders=orders,
    )

    await ensure_model_indexes(database)

    artifact_names = {call.kwargs["name"] for call in artifacts.create_index.await_args_list}
    version_names = {call.kwargs["name"] for call in versions.create_index.await_args_list}
    order_names = {call.kwargs["name"] for call in orders.create_index.await_args_list}
    assert "uniq_recommendation_artifact_model_product" in artifact_names
    assert "recommendation_artifact_model" in artifact_names
    assert "recommendation_model_built" in version_names
    assert order_names == {"orders_completed_training"}
    locks.create_index.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_switches_to_rollback_version_and_clears_result_cache() -> None:
    current = cpu_model("current")
    previous = cpu_model("previous")
    database = runtime_database(
        states=[{"modelVersion": "current"}, {"modelVersion": "previous"}],
        versions=[current.version_document(), previous.version_document()],
        artifacts=[
            [artifact.to_document() for artifact in current.artifacts],
            [artifact.to_document() for artifact in previous.artifacts],
        ],
    )
    runtime = RecommendationModelRuntime(db=database, settings=runtime_settings())

    assert (await runtime._active_model()).version == "current"  # type: ignore[union-attr]
    runtime._results[("u", "current", "trending", 1)] = SimpleNamespace(
        value=None, expires_at=0
    )
    runtime._has_polled = False

    rolled_back = await runtime._active_model()

    assert rolled_back is not None and rolled_back.version == "previous"
    assert not runtime._results


@pytest.mark.asyncio
async def test_runtime_keeps_last_known_good_when_new_pointer_is_invalid() -> None:
    current = cpu_model("current")
    database = runtime_database(
        states=[{"modelVersion": "current"}, {"modelVersion": "broken"}],
        versions=[current.version_document(), None],
        artifacts=[[artifact.to_document() for artifact in current.artifacts]],
    )
    runtime = RecommendationModelRuntime(db=database, settings=runtime_settings())
    loaded = await runtime._active_model()
    runtime._has_polled = False

    fallback = await runtime._active_model()

    assert loaded is not None
    assert fallback is loaded
    assert fallback.version == "current"


def test_runtime_rollout_bucket_accepts_any_pseudonym_format() -> None:
    runtime = RecommendationModelRuntime(
        db=SimpleNamespace(),
        settings=runtime_settings(recommendation_item_item_rollout_percent=50),
    )

    assert runtime._algorithm(user_ref="v2:not-hex", has_profile=False) == "trending"
    assert runtime._algorithm(user_ref="v2:not-hex", has_profile=True) in {
        "trending",
        "item_item",
    }


@pytest.mark.asyncio
async def test_failed_artifact_write_marks_version_failed_and_never_activates() -> None:
    build = cpu_model()
    versions = AsyncMock()
    versions.update_one.return_value = SimpleNamespace(matched_count=1)
    artifacts = AsyncMock()
    artifacts.bulk_write.side_effect = RuntimeError("storage failure")
    state = AsyncMock()
    database = FakeDatabase(
        recommendation_model_versions=versions,
        recommendation_artifacts=artifacts,
        recommendation_model_state=state,
    )

    with pytest.raises(RuntimeError, match="storage failure"):
        await write_model(
            database,
            build=build,
            offline_metrics={},
            activation_decision=ActivationDecision(True, ()),
            activate=True,
            max_artifact_bytes=1_000_000,
        )

    failure_update = versions.update_one.await_args_list[-1].args[1]["$set"]
    assert failure_update["status"] == "failed"
    artifacts.delete_many.assert_awaited_once_with({"modelVersion": build.version})
    state.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_oversized_model_is_not_stored_ready_or_activated() -> None:
    build = cpu_model()
    versions = AsyncMock()
    versions.update_one.return_value = SimpleNamespace(matched_count=1)
    artifacts = AsyncMock()
    state = AsyncMock()
    database = FakeDatabase(
        recommendation_model_versions=versions,
        recommendation_artifacts=artifacts,
        recommendation_model_state=state,
    )

    with pytest.raises(ModelBuildLimitError, match="runtime byte limit"):
        await write_model(
            database,
            build=build,
            offline_metrics={},
            activation_decision=ActivationDecision(False, ("oversized",)),
            activate=False,
            max_artifact_bytes=1,
        )

    artifacts.bulk_write.assert_not_awaited()
    state.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejected_shadow_model_cannot_be_rollback_target() -> None:
    build = cpu_model("rejected")
    version = build.version_document()
    version["activationEligibility"] = {"approved": False, "reasons": ["regression"]}
    version["hasBeenActive"] = False
    versions = AsyncMock()
    versions.find_one.return_value = version
    versions.update_one.return_value = SimpleNamespace(matched_count=1)
    state = AsyncMock()
    state.find_one.return_value = {"modelVersion": "active", "previousModelVersion": "old"}
    artifacts = MagicMock()
    database = FakeDatabase(
        recommendation_model_versions=versions,
        recommendation_model_state=state,
        recommendation_artifacts=artifacts,
    )

    with pytest.raises(RuntimeError, match="quality gate"):
        await activate_existing_model(
            database,
            "rejected",
            max_products=100,
            max_bytes=1_000_000,
        )

    artifacts.find.assert_not_called()
    state.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_previous_model_rolls_back_and_preserves_current_as_previous() -> None:
    build = cpu_model("previous")
    version = build.version_document()
    version["activationEligibility"] = {"approved": True, "reasons": []}
    version["hasBeenActive"] = True
    versions = AsyncMock()
    versions.find_one.return_value = version
    versions.update_one.return_value = SimpleNamespace(matched_count=1)
    state = AsyncMock()
    state.find_one.return_value = {
        "modelVersion": "current",
        "previousModelVersion": "previous",
    }
    artifacts = MagicMock()
    artifacts.find.return_value = AsyncListCursor(
        [artifact.to_document() for artifact in build.artifacts]
    )
    database = FakeDatabase(
        recommendation_model_versions=versions,
        recommendation_model_state=state,
        recommendation_artifacts=artifacts,
    )

    result = await activate_existing_model(
        database,
        "previous",
        max_products=100,
        max_bytes=1_000_000,
    )

    state_update = state.update_one.await_args.args[1]["$set"]
    assert result["previousModelVersion"] == "current"
    assert state_update["modelVersion"] == "previous"
    assert state_update["previousModelVersion"] == "current"
    versions.update_one.assert_awaited()


@pytest.mark.asyncio
async def test_cleanup_retains_active_and_previous_despite_newer_shadow_builds() -> None:
    old = NOW - timedelta(days=60)
    documents = [
        {"_id": "shadow-2", "status": "ready", "builtAt": old},
        {"_id": "shadow-1", "status": "ready", "builtAt": old},
        {"_id": "active", "status": "ready", "builtAt": old},
        {"_id": "previous", "status": "ready", "builtAt": old},
        {"_id": "obsolete", "status": "ready", "builtAt": old},
        {"_id": "abandoned", "status": "building", "builtAt": old},
    ]
    versions = MagicMock()
    versions.find.return_value = AsyncListCursor(documents)
    versions.delete_one = AsyncMock(return_value=SimpleNamespace(deleted_count=1))
    state = AsyncMock()
    state.find_one.return_value = {
        "modelVersion": "active",
        "previousModelVersion": "previous",
    }
    artifacts = AsyncMock()
    artifacts.delete_many.return_value = SimpleNamespace(deleted_count=2)
    database = FakeDatabase(
        recommendation_model_versions=versions,
        recommendation_model_state=state,
        recommendation_artifacts=artifacts,
    )

    result = await cleanup_old_models(database, retain_versions=2, retain_days=30)
    deleted = {call.args[0]["_id"] for call in versions.delete_one.await_args_list}

    assert deleted == {"obsolete", "abandoned"}
    assert result == {"versionsDeleted": 2, "artifactsDeleted": 4}


def test_activation_compares_item_item_with_trending_and_size_bound() -> None:
    interactions = [
        RecommendationInteraction("u1", "b", "purchase", NOW - timedelta(days=2), 1, "o1"),
        RecommendationInteraction("u1", "b", "purchase", NOW, 1, "o2"),
    ]
    trending = evaluate_temporal_split(
        interactions,
        catalog_product_ids={"a", "b"},
        cutoff=NOW - timedelta(days=1),
        algorithm="trending",
        config=ModelBuildConfig(min_pair_support=1),
    )
    regressed = replace(
        trending,
        algorithm="item_item",
        metrics={**trending.metrics, "ndcgAt10": 0.0},
    )
    decision = decide_build_activation(
        regressed,
        trending_baseline=trending,
        incumbent=None,
        gate=ActivationQualityGate(
            min_evaluation_users=1,
            min_catalog_coverage=0,
            max_ndcg_at_10_regression=0,
        ),
        artifact_bytes=2_000,
        max_artifact_bytes=1_000,
    )

    assert decision.approved is False
    assert any("trending baseline" in reason for reason in decision.reasons)
    assert any("artifact bytes" in reason for reason in decision.reasons)


def test_offline_metrics_include_recent_trending_and_popularity_comparisons() -> None:
    interactions = [
        RecommendationInteraction("u1", "a", "purchase", NOW - timedelta(days=2), 1, "o1"),
        RecommendationInteraction("u1", "b", "purchase", NOW, 1, "o2"),
    ]
    config = ModelBuildConfig(min_pair_support=1)
    item_item = evaluate_temporal_split(
        interactions,
        catalog_product_ids={"a", "b"},
        cutoff=NOW - timedelta(days=1),
        algorithm="item_item",
        config=config,
    )
    trending = evaluate_temporal_split(
        interactions,
        catalog_product_ids={"a", "b"},
        cutoff=NOW - timedelta(days=1),
        algorithm="trending",
        config=config,
    )
    recent = evaluate_temporal_split(
        interactions,
        catalog_product_ids={"a", "b"},
        cutoff=NOW - timedelta(days=1),
        algorithm="recent",
        recent_product_ids=["b", "a"],
        config=config,
    )

    document = offline_metrics_document(item_item, trending, recent)

    assert "trendingBaseline" in document
    assert "recentBaseline" in document
    assert "popularityShare" in document
    assert set(document["comparison"]) == {
        "itemItemMinusTrending",
        "itemItemMinusRecent",
    }
