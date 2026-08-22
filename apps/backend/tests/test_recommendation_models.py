from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.recommendation_models import (
    ActivationQualityGate,
    ModelBuildConfig,
    ModelBuildLimitError,
    RecommendationInteraction,
    build_cpu_recommendation_model,
    decide_model_activation,
    evaluate_temporal_split,
    load_cpu_recommendation_model,
    rank_products,
)
from scripts.build_recommendation_model import load_recent_product_ids

NOW = datetime(2026, 8, 22, 12, tzinfo=UTC)


def interaction(
    user: str,
    product: str,
    *,
    days_ago: float = 0,
    event_type: str = "purchase",
    order_id: str | None = None,
) -> RecommendationInteraction:
    return RecommendationInteraction(
        user_ref=user,
        product_id=product,
        event_type=event_type,
        occurred_at=NOW - timedelta(days=days_ago),
        order_id=order_id,
    )


def test_trending_uses_time_decay_and_server_event_weights() -> None:
    config = ModelBuildConfig(
        trending_half_life_days=7,
        min_pair_support=1,
    )
    model = build_cpu_recommendation_model(
        [
            interaction("u1", "recent", order_id="o1"),
            interaction("u2", "old", days_ago=14, order_id="o2"),
            interaction("u3", "click", event_type="click"),
        ],
        catalog_product_ids={"recent", "old", "click"},
        training_cutoff=NOW,
        config=config,
    )
    scores = {artifact.product_id: artifact.trending_score for artifact in model.artifacts}

    assert scores["recent"] == pytest.approx(6.0)
    assert scores["old"] == pytest.approx(1.5)
    assert scores["click"] == pytest.approx(0.25)
    assert rank_products(model, profile_product_ids=[], limit=3, algorithm="trending") == [
        "recent",
        "old",
        "click",
    ]


def test_impressions_are_an_exposure_denominator_not_a_positive_signal() -> None:
    interactions = [
        interaction("u1", "low-exposure", event_type="click"),
        interaction("u2", "high-exposure", event_type="click"),
    ]
    interactions.extend(
        interaction(f"viewer-{index}", "high-exposure", event_type="impression")
        for index in range(10)
    )
    model = build_cpu_recommendation_model(
        interactions,
        catalog_product_ids={"low-exposure", "high-exposure", "only-impressions"},
        training_cutoff=NOW,
        config=ModelBuildConfig(min_pair_support=1),
    )
    scores = {artifact.product_id: artifact.trending_score for artifact in model.artifacts}

    assert scores["low-exposure"] == pytest.approx(0.25)
    assert scores["high-exposure"] == pytest.approx(0.025)
    assert model.configuration.impression_weight == 0
    assert model.stats.impressions_used == 10


def test_item_item_uses_completed_order_baskets_and_cosine_similarity() -> None:
    model = build_cpu_recommendation_model(
        [
            interaction("u1", "a", order_id="o1"),
            interaction("u1", "b", order_id="o1"),
            interaction("u2", "a", order_id="o2"),
            interaction("u2", "b", order_id="o2"),
            interaction("u2", "c", order_id="o2"),
            interaction("u3", "a", order_id="o3"),
            interaction("u3", "b", order_id="o3"),
        ],
        catalog_product_ids={"a", "b", "c"},
        training_cutoff=NOW,
        config=ModelBuildConfig(min_pair_support=2, top_neighbors=5),
    )
    artifacts = model.artifact_map()

    neighbors = [
        (neighbor.product_id, neighbor.support) for neighbor in artifacts["a"].neighbors
    ]
    assert neighbors == [("b", 3.0)]
    assert artifacts["a"].neighbors[0].score == pytest.approx(1.0)
    assert rank_products(model, profile_product_ids=["a"], limit=1) == ["b"]
    assert model.stats.purchase_baskets == 3


def test_same_user_lifetime_pairs_are_disabled_unless_explicitly_enabled() -> None:
    interactions = [
        interaction("u1", "a", days_ago=2, order_id="o1"),
        interaction("u1", "b", days_ago=1, order_id="o2"),
    ]
    default_model = build_cpu_recommendation_model(
        interactions,
        catalog_product_ids={"a", "b"},
        training_cutoff=NOW,
        config=ModelBuildConfig(min_pair_support=1),
    )
    history_model = build_cpu_recommendation_model(
        interactions,
        catalog_product_ids={"a", "b"},
        training_cutoff=NOW,
        config=ModelBuildConfig(
            min_pair_support=1,
            same_user_history_pair_weight=1,
        ),
    )

    assert default_model.artifact_map()["a"].neighbors == ()
    assert history_model.artifact_map()["a"].neighbors[0].product_id == "b"


def test_top_neighbors_and_stream_size_are_hard_bounded() -> None:
    model = build_cpu_recommendation_model(
        [
            interaction("u1", "a", order_id="o1"),
            interaction("u1", "b", order_id="o1"),
            interaction("u2", "a", order_id="o2"),
            interaction("u2", "c", order_id="o2"),
        ],
        catalog_product_ids={"a", "b", "c"},
        training_cutoff=NOW,
        config=ModelBuildConfig(min_pair_support=1, top_neighbors=1),
    )
    assert len(model.artifact_map()["a"].neighbors) == 1

    with pytest.raises(ModelBuildLimitError, match="max_interactions"):
        build_cpu_recommendation_model(
            [
                interaction("u1", "a", order_id="o1"),
                interaction("u1", "b", order_id="o1"),
                interaction("u2", "c", order_id="o2"),
            ],
            catalog_product_ids={"a", "b", "c"},
            training_cutoff=NOW,
            config=ModelBuildConfig(max_interactions=2),
        )


def test_global_temporal_split_does_not_train_on_future_purchases() -> None:
    cutoff = NOW - timedelta(days=1)
    result = evaluate_temporal_split(
        [
            interaction("warm", "a", days_ago=3, order_id="o1"),
            interaction("other", "a", days_ago=2, order_id="o2"),
            interaction("warm", "b", days_ago=0, order_id="future-1"),
            interaction("cold", "b", days_ago=0, order_id="future-2"),
        ],
        catalog_product_ids={"a", "b"},
        cutoff=cutoff,
        config=ModelBuildConfig(min_pair_support=1),
        algorithm="trending",
    )

    assert set(result.build.artifact_map()) == {"a"}
    assert result.users == 2
    assert result.warm.users == 1
    assert result.cold.users == 1
    assert set(result.metrics) == {"recallAt5", "ndcgAt5", "recallAt10", "ndcgAt10"}
    assert result.metrics["recallAt10"] == 0.0
    assert result.catalog_coverage == pytest.approx(0.5)
    assert 0 <= result.popularity_share <= 1


def test_recent_baseline_is_deterministic_on_the_same_temporal_split() -> None:
    result = evaluate_temporal_split(
        [
            interaction("warm", "old", days_ago=3, order_id="o1"),
            interaction("warm", "new", days_ago=0, order_id="o2"),
        ],
        catalog_product_ids={"old", "new"},
        cutoff=NOW - timedelta(days=1),
        algorithm="recent",
        recent_product_ids=["new", "old"],
        config=ModelBuildConfig(min_pair_support=1),
    )

    assert result.algorithm == "recent"
    assert result.metrics["recallAt5"] == 1.0
    assert result.metrics["ndcgAt5"] == 1.0
    assert result.catalog_coverage == 1.0


@pytest.mark.asyncio
async def test_recent_baseline_catalog_is_bounded_by_evaluation_cutoff() -> None:
    products = Mock()
    cursor = Mock()
    products.find.return_value = cursor
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[{"_id": "known-product"}])
    cutoff = NOW - timedelta(days=1)

    product_ids = await load_recent_product_ids(
        products,
        max_products=10,
        created_before=cutoff,
    )

    assert product_ids == ["known-product"]
    query = products.find.call_args.args[0]
    assert query["$or"][0] == {"createdAt": {"$lte": cutoff}}


def test_loader_round_trip_and_rejects_malformed_or_oversized_artifacts() -> None:
    model = build_cpu_recommendation_model(
        [interaction("u1", "a", order_id="o1")],
        catalog_product_ids={"a"},
        training_cutoff=NOW,
        config=ModelBuildConfig(min_pair_support=1),
        version="version-1",
    )
    version_document = model.version_document()
    artifact_documents = [artifact.to_document() for artifact in model.artifacts]

    loaded = load_cpu_recommendation_model(version_document, artifact_documents)

    assert loaded.version == model.version
    assert loaded.artifacts == model.artifacts
    with pytest.raises(ValueError, match="modelVersion"):
        load_cpu_recommendation_model(
            version_document,
            [{**artifact_documents[0], "modelVersion": "other"}],
        )
    with pytest.raises(ModelBuildLimitError, match="serialized"):
        load_cpu_recommendation_model(
            version_document,
            artifact_documents,
            max_serialized_bytes=1,
        )


def test_activation_gate_preserves_incumbent_on_quality_regression() -> None:
    cutoff = NOW - timedelta(days=1)
    evaluation = evaluate_temporal_split(
        [
            interaction("u1", "a", days_ago=2, order_id="o1"),
            interaction("u1", "a", days_ago=0, order_id="o2"),
        ],
        catalog_product_ids={"a"},
        cutoff=cutoff,
        config=ModelBuildConfig(min_pair_support=1),
        algorithm="trending",
    )
    approved = decide_model_activation(
        evaluation,
        gate=ActivationQualityGate(
            min_evaluation_users=1,
            min_catalog_coverage=1,
        ),
    )
    rejected = decide_model_activation(
        evaluation,
        incumbent_metrics={"ndcgAt10": 1.5},
        gate=ActivationQualityGate(
            min_evaluation_users=1,
            min_catalog_coverage=1,
            max_ndcg_at_10_regression=0,
        ),
    )

    assert approved.approved is True
    assert rejected.approved is False
    assert any("quality floor" in reason for reason in rejected.reasons)
