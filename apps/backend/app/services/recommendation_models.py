from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

RecommendationAlgorithm = Literal["trending", "item_item"]
EvaluationAlgorithm = Literal["recent", "trending", "item_item"]


class ModelBuildLimitError(RuntimeError):
    """Raised before an offline build can exceed its configured CPU or memory bound."""


@dataclass(frozen=True, slots=True)
class RecommendationInteraction:
    user_ref: str
    product_id: str
    event_type: str
    occurred_at: datetime
    quantity: int = 1
    order_id: str | None = None


@dataclass(frozen=True, slots=True)
class ModelBuildConfig:
    training_window_days: int = 180
    trending_half_life_days: float = 14.0
    max_interactions: int = 250_000
    max_catalog_products: int = 10_000
    max_users: int = 100_000
    max_history_per_user: int = 20
    max_basket_items: int = 20
    max_pair_entries: int = 500_000
    min_pair_support: int = 3
    top_neighbors: int = 50
    max_candidates: int = 200
    impression_weight: float = 0.0
    click_weight: float = 0.25
    add_to_cart_weight: float = 1.0
    purchase_weight: float = 6.0
    trending_blend: float = 0.25
    same_user_history_pair_weight: float = 0.0

    def validate(self) -> None:
        positive_values = {
            "training_window_days": self.training_window_days,
            "trending_half_life_days": self.trending_half_life_days,
            "max_interactions": self.max_interactions,
            "max_catalog_products": self.max_catalog_products,
            "max_users": self.max_users,
            "max_history_per_user": self.max_history_per_user,
            "max_basket_items": self.max_basket_items,
            "max_pair_entries": self.max_pair_entries,
            "min_pair_support": self.min_pair_support,
            "top_neighbors": self.top_neighbors,
            "max_candidates": self.max_candidates,
        }
        invalid = [name for name, value in positive_values.items() if value <= 0]
        if invalid:
            raise ValueError(f"Model build limits must be positive: {', '.join(invalid)}")
        if not 0 <= self.trending_blend <= 1:
            raise ValueError("trending_blend must be between 0 and 1")
        if not 0 <= self.same_user_history_pair_weight <= 1:
            raise ValueError("same_user_history_pair_weight must be between 0 and 1")

    def event_weight(self, event_type: str) -> float:
        return {
            "impression": self.impression_weight,
            "click": self.click_weight,
            "add_to_cart": self.add_to_cart_weight,
            "purchase": self.purchase_weight,
        }.get(event_type, 0.0)


@dataclass(frozen=True, slots=True)
class ItemNeighbor:
    product_id: str
    score: float
    support: float

    def to_document(self) -> dict[str, object]:
        return {
            "productId": self.product_id,
            "score": round(self.score, 8),
            "support": round(self.support, 4),
        }


@dataclass(frozen=True, slots=True)
class ProductModelArtifact:
    model_version: str
    product_id: str
    trending_score: float
    neighbors: tuple[ItemNeighbor, ...]

    def to_document(self) -> dict[str, object]:
        return {
            "modelVersion": self.model_version,
            "productId": self.product_id,
            "trendingScore": round(self.trending_score, 8),
            "neighbors": [neighbor.to_document() for neighbor in self.neighbors],
        }


@dataclass(frozen=True, slots=True)
class ModelBuildStats:
    interactions_seen: int
    interactions_used: int
    impressions_used: int
    purchase_users: int
    purchase_baskets: int
    catalog_products: int
    pair_entries: int
    artifacts: int
    approximate_artifact_bytes: int
    build_duration_ms: float

    def to_document(self) -> dict[str, int | float]:
        return {
            "interactionsSeen": self.interactions_seen,
            "interactionsUsed": self.interactions_used,
            "impressionsUsed": self.impressions_used,
            "purchaseUsers": self.purchase_users,
            "purchaseBaskets": self.purchase_baskets,
            "catalogProducts": self.catalog_products,
            "pairEntries": self.pair_entries,
            "artifacts": self.artifacts,
            "approximateArtifactBytes": self.approximate_artifact_bytes,
            "buildDurationMs": round(self.build_duration_ms, 3),
        }


@dataclass(frozen=True, slots=True)
class CPUModelBuildResult:
    version: str
    built_at: datetime
    training_cutoff: datetime
    algorithm: str
    configuration: ModelBuildConfig
    artifacts: tuple[ProductModelArtifact, ...]
    stats: ModelBuildStats

    def artifact_map(self) -> dict[str, ProductModelArtifact]:
        return {artifact.product_id: artifact for artifact in self.artifacts}

    def version_document(
        self,
        *,
        status: str = "ready",
        offline_metrics: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "_id": self.version,
            "algorithm": self.algorithm,
            "status": status,
            "builtAt": self.built_at,
            "trainingCutoff": self.training_cutoff,
            "configuration": asdict(self.configuration),
            "stats": self.stats.to_document(),
            "offlineMetrics": offline_metrics or {},
        }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class CPUModelAccumulator:
    """Consumes a bounded interaction stream and produces immutable CPU artifacts."""

    def __init__(
        self,
        *,
        catalog_product_ids: Iterable[str],
        training_cutoff: datetime,
        config: ModelBuildConfig,
        version: str | None = None,
    ) -> None:
        config.validate()
        catalog = frozenset(catalog_product_ids)
        if len(catalog) > config.max_catalog_products:
            raise ModelBuildLimitError(
                f"Catalog exceeds max_catalog_products={config.max_catalog_products}"
            )
        self.catalog = catalog
        self.training_cutoff = _as_utc(training_cutoff)
        self.window_start = self.training_cutoff - timedelta(days=config.training_window_days)
        self.config = config
        self.version = version or uuid4().hex
        self.interactions_seen = 0
        self.interactions_used = 0
        self._purchase_scores: defaultdict[str, float] = defaultdict(float)
        self._engagement_scores: defaultdict[str, float] = defaultdict(float)
        self._exposure_scores: defaultdict[str, float] = defaultdict(float)
        self._impressions_used = 0
        self._purchase_histories: dict[str, dict[str, datetime]] = {}
        self._purchase_baskets: dict[str, dict[str, datetime]] = {}
        self._started_at = time.perf_counter()

    def consume(self, interaction: RecommendationInteraction) -> None:
        self.interactions_seen += 1
        if self.interactions_seen > self.config.max_interactions:
            raise ModelBuildLimitError(
                f"Interactions exceed max_interactions={self.config.max_interactions}"
            )

        occurred_at = _as_utc(interaction.occurred_at)
        if not self.window_start <= occurred_at <= self.training_cutoff:
            return
        if interaction.product_id not in self.catalog:
            return
        age_days = (self.training_cutoff - occurred_at).total_seconds() / 86_400
        decay = 0.5 ** (age_days / self.config.trending_half_life_days)
        quantity = max(1, interaction.quantity)
        if interaction.event_type == "impression":
            self.interactions_used += 1
            self._impressions_used += quantity
            self._exposure_scores[interaction.product_id] += quantity * decay
            return
        event_weight = self.config.event_weight(interaction.event_type)
        if event_weight <= 0:
            return

        self.interactions_used += 1
        weighted_signal = event_weight * quantity * decay
        if interaction.event_type == "purchase":
            self._purchase_scores[interaction.product_id] += weighted_signal
        else:
            self._engagement_scores[interaction.product_id] += weighted_signal

        if interaction.event_type != "purchase" or not interaction.user_ref:
            return
        if interaction.order_id:
            basket = self._purchase_baskets.setdefault(interaction.order_id, {})
            previous_in_basket = basket.get(interaction.product_id)
            if previous_in_basket is not None:
                basket[interaction.product_id] = max(previous_in_basket, occurred_at)
            elif len(basket) < self.config.max_basket_items:
                basket[interaction.product_id] = occurred_at

        history = self._purchase_histories.get(interaction.user_ref)
        if history is None:
            if len(self._purchase_histories) >= self.config.max_users:
                raise ModelBuildLimitError(
                    f"Purchase users exceed max_users={self.config.max_users}"
                )
            history = {}
            self._purchase_histories[interaction.user_ref] = history
        previous = history.get(interaction.product_id)
        if previous is not None:
            history[interaction.product_id] = max(previous, occurred_at)
            return
        if len(history) < self.config.max_history_per_user:
            history[interaction.product_id] = occurred_at
            return
        oldest_product = min(history, key=history.__getitem__)
        if occurred_at > history[oldest_product]:
            del history[oldest_product]
            history[interaction.product_id] = occurred_at

    def profile_for(self, user_ref: str) -> tuple[str, ...]:
        history = self._purchase_histories.get(user_ref, {})
        ordered = sorted(history.items(), key=lambda item: (-item[1].timestamp(), item[0]))
        return tuple(product_id for product_id, _occurred_at in ordered)

    def finalize(self) -> CPUModelBuildResult:
        item_support: defaultdict[str, float] = defaultdict(float)
        pair_support: defaultdict[tuple[str, str], float] = defaultdict(float)

        def record_pairs(product_ids: list[str], weight: float) -> None:
            for product_id in product_ids:
                item_support[product_id] += weight
            for left_index, left in enumerate(product_ids):
                for right in product_ids[left_index + 1 :]:
                    pair = (left, right)
                    if pair not in pair_support and len(pair_support) >= (
                        self.config.max_pair_entries
                    ):
                        raise ModelBuildLimitError(
                            "Co-occurrence pairs exceed "
                            f"max_pair_entries={self.config.max_pair_entries}"
                        )
                    pair_support[pair] += weight

        for basket in self._purchase_baskets.values():
            record_pairs(sorted(basket), 1.0)
        if self.config.same_user_history_pair_weight > 0:
            for history in self._purchase_histories.values():
                record_pairs(
                    sorted(history),
                    self.config.same_user_history_pair_weight,
                )

        neighbors: defaultdict[str, list[ItemNeighbor]] = defaultdict(list)
        for (left, right), support in pair_support.items():
            if support < self.config.min_pair_support:
                continue
            denominator = math.sqrt(item_support[left] * item_support[right])
            if denominator <= 0:
                continue
            score = support / denominator
            neighbors[left].append(ItemNeighbor(right, score, support))
            neighbors[right].append(ItemNeighbor(left, score, support))

        for product_neighbors in neighbors.values():
            product_neighbors.sort(key=lambda item: (-item.score, item.product_id))
            del product_neighbors[self.config.top_neighbors :]

        scored_products = (
            set(self._purchase_scores)
            | set(self._engagement_scores)
            | set(self._exposure_scores)
        )
        artifact_products = scored_products | set(neighbors)

        def trending_score(product_id: str) -> float:
            exposure = self._exposure_scores.get(product_id, 0.0)
            engagement_denominator = max(1.0, exposure)
            return self._purchase_scores.get(product_id, 0.0) + (
                self._engagement_scores.get(product_id, 0.0) / engagement_denominator
            )

        artifacts = tuple(
            ProductModelArtifact(
                model_version=self.version,
                product_id=product_id,
                trending_score=trending_score(product_id),
                neighbors=tuple(neighbors.get(product_id, ())),
            )
            for product_id in sorted(artifact_products)
        )
        approximate_bytes = sum(
            len(json.dumps(artifact.to_document(), separators=(",", ":")))
            for artifact in artifacts
        )
        duration_ms = (time.perf_counter() - self._started_at) * 1_000
        stats = ModelBuildStats(
            interactions_seen=self.interactions_seen,
            interactions_used=self.interactions_used,
            impressions_used=self._impressions_used,
            purchase_users=len(self._purchase_histories),
            purchase_baskets=len(self._purchase_baskets),
            catalog_products=len(self.catalog),
            pair_entries=len(pair_support),
            artifacts=len(artifacts),
            approximate_artifact_bytes=approximate_bytes,
            build_duration_ms=duration_ms,
        )
        return CPUModelBuildResult(
            version=self.version,
            built_at=datetime.now(UTC),
            training_cutoff=self.training_cutoff,
            algorithm="cpu_trending_item_item_v1",
            configuration=self.config,
            artifacts=artifacts,
            stats=stats,
        )


def build_cpu_recommendation_model(
    interactions: Iterable[RecommendationInteraction],
    *,
    catalog_product_ids: Iterable[str],
    training_cutoff: datetime,
    config: ModelBuildConfig | None = None,
    version: str | None = None,
) -> CPUModelBuildResult:
    resolved_config = config or ModelBuildConfig()
    accumulator = CPUModelAccumulator(
        catalog_product_ids=catalog_product_ids,
        training_cutoff=training_cutoff,
        config=resolved_config,
        version=version,
    )
    for interaction in interactions:
        accumulator.consume(interaction)
    return accumulator.finalize()


def load_cpu_recommendation_model(
    version_document: dict[str, object],
    artifact_documents: Iterable[dict[str, object]],
    *,
    max_serialized_bytes: int = 10_000_000,
) -> CPUModelBuildResult:
    """Reconstruct and validate a bounded immutable model read from MongoDB."""

    if max_serialized_bytes <= 0:
        raise ValueError("max_serialized_bytes must be positive")
    version = version_document.get("_id")
    if not isinstance(version, str) or not version:
        raise ValueError("Model version document has no valid _id")
    built_at = version_document.get("builtAt")
    training_cutoff = version_document.get("trainingCutoff")
    if not isinstance(built_at, datetime) or not isinstance(training_cutoff, datetime):
        raise ValueError("Model version timestamps must be datetime values")

    raw_config = version_document.get("configuration")
    if not isinstance(raw_config, dict):
        raise ValueError("Model version configuration must be an object")
    defaults = ModelBuildConfig()

    def config_number(name: str, default: int | float) -> int | float:
        value = raw_config.get(name, default)
        if not isinstance(value, int | float):
            raise ValueError(f"Model configuration value {name} must be numeric")
        return value

    config = ModelBuildConfig(
        training_window_days=int(
            config_number("training_window_days", defaults.training_window_days)
        ),
        trending_half_life_days=float(
            config_number("trending_half_life_days", defaults.trending_half_life_days)
        ),
        max_interactions=int(config_number("max_interactions", defaults.max_interactions)),
        max_catalog_products=int(
            config_number("max_catalog_products", defaults.max_catalog_products)
        ),
        max_users=int(config_number("max_users", defaults.max_users)),
        max_history_per_user=int(
            config_number("max_history_per_user", defaults.max_history_per_user)
        ),
        max_basket_items=int(config_number("max_basket_items", defaults.max_basket_items)),
        max_pair_entries=int(config_number("max_pair_entries", defaults.max_pair_entries)),
        min_pair_support=int(config_number("min_pair_support", defaults.min_pair_support)),
        top_neighbors=int(config_number("top_neighbors", defaults.top_neighbors)),
        max_candidates=int(config_number("max_candidates", defaults.max_candidates)),
        impression_weight=float(config_number("impression_weight", defaults.impression_weight)),
        click_weight=float(config_number("click_weight", defaults.click_weight)),
        add_to_cart_weight=float(
            config_number("add_to_cart_weight", defaults.add_to_cart_weight)
        ),
        purchase_weight=float(config_number("purchase_weight", defaults.purchase_weight)),
        trending_blend=float(config_number("trending_blend", defaults.trending_blend)),
        same_user_history_pair_weight=float(
            config_number(
                "same_user_history_pair_weight",
                defaults.same_user_history_pair_weight,
            )
        ),
    )
    config.validate()

    artifacts: list[ProductModelArtifact] = []
    seen_products: set[str] = set()
    serialized_bytes = 0
    for raw_artifact in artifact_documents:
        if len(artifacts) >= config.max_catalog_products:
            raise ModelBuildLimitError("Stored model exceeds max_catalog_products")
        if raw_artifact.get("modelVersion") != version:
            raise ValueError("Artifact modelVersion does not match version document")
        product_id = raw_artifact.get("productId")
        if not isinstance(product_id, str) or not product_id or product_id in seen_products:
            raise ValueError("Artifact productId is invalid or duplicated")
        seen_products.add(product_id)
        raw_trending_score = raw_artifact.get("trendingScore", 0.0)
        if not isinstance(raw_trending_score, int | float):
            raise ValueError("Artifact trendingScore is invalid")
        trending_score = float(raw_trending_score)
        if not math.isfinite(trending_score) or trending_score < 0:
            raise ValueError("Artifact trendingScore must be finite and non-negative")

        raw_neighbors = raw_artifact.get("neighbors", [])
        if not isinstance(raw_neighbors, list) or len(raw_neighbors) > config.top_neighbors:
            raise ModelBuildLimitError("Artifact exceeds the configured neighbor bound")
        neighbors: list[ItemNeighbor] = []
        neighbor_products: set[str] = set()
        for raw_neighbor in raw_neighbors:
            if not isinstance(raw_neighbor, dict):
                raise ValueError("Artifact neighbor must be an object")
            neighbor_product = raw_neighbor.get("productId")
            if (
                not isinstance(neighbor_product, str)
                or not neighbor_product
                or neighbor_product == product_id
                or neighbor_product in neighbor_products
            ):
                raise ValueError("Artifact neighbor productId is invalid or duplicated")
            try:
                score = float(raw_neighbor.get("score", 0.0))
                support = float(raw_neighbor.get("support", 0.0))
            except (TypeError, ValueError) as exc:
                raise ValueError("Artifact neighbor score or support is invalid") from exc
            invalid_score = not math.isfinite(score) or score < 0
            invalid_support = not math.isfinite(support) or support < 0
            if invalid_score or invalid_support:
                raise ValueError("Artifact neighbor values must be finite and non-negative")
            neighbor_products.add(neighbor_product)
            neighbors.append(ItemNeighbor(neighbor_product, score, support))
        neighbors.sort(key=lambda item: (-item.score, item.product_id))
        artifact = ProductModelArtifact(
            model_version=version,
            product_id=product_id,
            trending_score=trending_score,
            neighbors=tuple(neighbors),
        )
        serialized_bytes += len(json.dumps(artifact.to_document(), separators=(",", ":")))
        if serialized_bytes > max_serialized_bytes:
            raise ModelBuildLimitError("Stored model exceeds max_serialized_bytes")
        artifacts.append(artifact)

    unknown_neighbors = {
        neighbor.product_id
        for artifact in artifacts
        for neighbor in artifact.neighbors
        if neighbor.product_id not in seen_products
    }
    if unknown_neighbors:
        raise ValueError("Artifact contains neighbors missing from the model version")

    raw_stats = version_document.get("stats")
    stats_document = raw_stats if isinstance(raw_stats, dict) else {}
    expected_artifacts = stats_document.get("artifacts")
    if isinstance(expected_artifacts, int | float) and int(expected_artifacts) != len(
        artifacts
    ):
        raise ValueError("Stored artifact count does not match model metadata")

    def stat_int(name: str, default: int) -> int:
        value = stats_document.get(name, default)
        return int(value) if isinstance(value, int | float) else default

    duration = stats_document.get("buildDurationMs", 0.0)
    stats = ModelBuildStats(
        interactions_seen=stat_int("interactionsSeen", 0),
        interactions_used=stat_int("interactionsUsed", 0),
        impressions_used=stat_int("impressionsUsed", 0),
        purchase_users=stat_int("purchaseUsers", 0),
        purchase_baskets=stat_int("purchaseBaskets", 0),
        catalog_products=stat_int("catalogProducts", len(artifacts)),
        pair_entries=stat_int("pairEntries", 0),
        artifacts=len(artifacts),
        approximate_artifact_bytes=serialized_bytes,
        build_duration_ms=float(duration) if isinstance(duration, int | float) else 0.0,
    )
    algorithm = version_document.get("algorithm")
    return CPUModelBuildResult(
        version=version,
        built_at=_as_utc(built_at),
        training_cutoff=_as_utc(training_cutoff),
        algorithm=algorithm if isinstance(algorithm, str) else "cpu_trending_item_item_v1",
        configuration=config,
        artifacts=tuple(sorted(artifacts, key=lambda artifact: artifact.product_id)),
        stats=stats,
    )


def rank_products(
    model: CPUModelBuildResult,
    *,
    profile_product_ids: Iterable[str],
    limit: int,
    algorithm: RecommendationAlgorithm = "item_item",
) -> list[str]:
    if limit <= 0:
        return []
    resolved_limit = min(limit, model.configuration.max_candidates)
    artifacts = model.artifact_map()
    trending = sorted(
        artifacts.values(),
        key=lambda artifact: (-artifact.trending_score, artifact.product_id),
    )
    if algorithm == "trending":
        return [artifact.product_id for artifact in trending[:resolved_limit]]

    max_trending = max((artifact.trending_score for artifact in trending), default=0.0)
    scores: defaultdict[str, float] = defaultdict(float)
    for artifact in trending[: model.configuration.max_candidates]:
        normalized = artifact.trending_score / max_trending if max_trending else 0.0
        scores[artifact.product_id] += normalized * model.configuration.trending_blend

    profile = tuple(dict.fromkeys(profile_product_ids))[
        : model.configuration.max_history_per_user
    ]
    personalized_weight = 1 - model.configuration.trending_blend
    for product_id in profile:
        profile_artifact = artifacts.get(product_id)
        if profile_artifact is None:
            continue
        for neighbor in profile_artifact.neighbors:
            scores[neighbor.product_id] += neighbor.score * personalized_weight

    ranked = sorted(scores, key=lambda product_id: (-scores[product_id], product_id))[
        : model.configuration.max_candidates
    ]
    if len(ranked) < resolved_limit:
        present = set(ranked)
        ranked.extend(
            artifact.product_id for artifact in trending if artifact.product_id not in present
        )
    return ranked[:resolved_limit]


def recall_at_k(recommended: list[str], relevant: set[str], k: int) -> float:
    if not relevant or k <= 0:
        return 0.0
    return len(set(recommended[:k]) & relevant) / len(relevant)


def ndcg_at_k(recommended: list[str], relevant: set[str], k: int) -> float:
    if not relevant or k <= 0:
        return 0.0
    dcg = sum(
        1.0 / math.log2(rank + 2)
        for rank, product_id in enumerate(recommended[:k])
        if product_id in relevant
    )
    ideal_hits = min(len(relevant), k)
    ideal = sum(1.0 / math.log2(rank + 2) for rank in range(ideal_hits))
    return dcg / ideal if ideal else 0.0


@dataclass(frozen=True, slots=True)
class CohortMetrics:
    users: int
    metrics: dict[str, float]

    def to_document(self) -> dict[str, object]:
        return {"users": self.users, **self.metrics}


@dataclass(frozen=True, slots=True)
class TemporalEvaluationResult:
    algorithm: EvaluationAlgorithm
    cutoff: datetime
    users: int
    metrics: dict[str, float]
    catalog_coverage: float
    popularity_share: float
    warm: CohortMetrics
    cold: CohortMetrics
    build: CPUModelBuildResult

    def to_document(self) -> dict[str, object]:
        return {
            "algorithm": self.algorithm,
            "cutoff": self.cutoff,
            "users": self.users,
            **self.metrics,
            "catalogCoverage": self.catalog_coverage,
            "popularityShare": self.popularity_share,
            "warm": self.warm.to_document(),
            "cold": self.cold.to_document(),
            "buildStats": self.build.stats.to_document(),
        }


def _average_metrics(
    rows: list[tuple[list[str], set[str]]], ks: tuple[int, ...]
) -> dict[str, float]:
    if not rows:
        return {metric_name: 0.0 for k in ks for metric_name in (f"recallAt{k}", f"ndcgAt{k}")}
    output: dict[str, float] = {}
    for k in ks:
        output[f"recallAt{k}"] = sum(
            recall_at_k(recommended, relevant, k) for recommended, relevant in rows
        ) / len(rows)
        output[f"ndcgAt{k}"] = sum(
            ndcg_at_k(recommended, relevant, k) for recommended, relevant in rows
        ) / len(rows)
    return output


def evaluate_temporal_split(
    interactions: Iterable[RecommendationInteraction],
    *,
    catalog_product_ids: Iterable[str],
    cutoff: datetime,
    config: ModelBuildConfig | None = None,
    algorithm: EvaluationAlgorithm = "item_item",
    ks: tuple[int, ...] = (5, 10),
    recent_product_ids: Iterable[str] | None = None,
) -> TemporalEvaluationResult:
    if not ks or any(k <= 0 for k in ks):
        raise ValueError("ks must contain positive values")
    resolved_config = config or ModelBuildConfig()
    catalog = frozenset(catalog_product_ids)
    resolved_cutoff = _as_utc(cutoff)
    accumulator = CPUModelAccumulator(
        catalog_product_ids=catalog,
        training_cutoff=resolved_cutoff,
        config=resolved_config,
    )
    relevant_by_user: defaultdict[str, set[str]] = defaultdict(set)
    total_seen = 0
    for interaction in interactions:
        total_seen += 1
        if total_seen > resolved_config.max_interactions:
            raise ModelBuildLimitError(
                f"Evaluation exceeds max_interactions={resolved_config.max_interactions}"
            )
        occurred_at = _as_utc(interaction.occurred_at)
        if occurred_at <= resolved_cutoff:
            accumulator.consume(interaction)
        elif (
            interaction.event_type == "purchase"
            and interaction.product_id in catalog
            and interaction.user_ref
        ):
            relevant_by_user[interaction.user_ref].add(interaction.product_id)

    model = accumulator.finalize()
    max_k = max(ks)
    recent_ranking = tuple(
        product_id
        for product_id in dict.fromkeys(recent_product_ids or sorted(catalog))
        if product_id in catalog
    )
    all_rows: list[tuple[list[str], set[str]]] = []
    warm_rows: list[tuple[list[str], set[str]]] = []
    cold_rows: list[tuple[list[str], set[str]]] = []
    covered_products: set[str] = set()
    popular_product_count = max(1, math.ceil(len(catalog) * 0.1)) if catalog else 0
    popular_products = {
        product_id
        for product_id in rank_products(
            model,
            profile_product_ids=(),
            limit=popular_product_count,
            algorithm="trending",
        )
    }
    popular_slots = 0
    recommendation_slots = 0
    for user_ref, relevant in sorted(relevant_by_user.items()):
        profile = accumulator.profile_for(user_ref)
        if algorithm == "recent":
            recommended = list(recent_ranking[:max_k])
        else:
            recommended = rank_products(
                model,
                profile_product_ids=profile,
                limit=max_k,
                algorithm=algorithm,
            )
        row = (recommended, relevant)
        all_rows.append(row)
        covered_products.update(recommended)
        popular_slots += sum(product_id in popular_products for product_id in recommended)
        recommendation_slots += len(recommended)
        (warm_rows if profile else cold_rows).append(row)

    coverage = len(covered_products) / len(catalog) if catalog else 0.0
    return TemporalEvaluationResult(
        algorithm=algorithm,
        cutoff=resolved_cutoff,
        users=len(all_rows),
        metrics=_average_metrics(all_rows, ks),
        catalog_coverage=coverage,
        popularity_share=(
            popular_slots / recommendation_slots if recommendation_slots else 0.0
        ),
        warm=CohortMetrics(len(warm_rows), _average_metrics(warm_rows, ks)),
        cold=CohortMetrics(len(cold_rows), _average_metrics(cold_rows, ks)),
        build=model,
    )


@dataclass(frozen=True, slots=True)
class ActivationQualityGate:
    min_evaluation_users: int = 5
    min_catalog_coverage: float = 0.05
    max_ndcg_at_10_regression: float = 0.02


@dataclass(frozen=True, slots=True)
class ActivationDecision:
    approved: bool
    reasons: tuple[str, ...]

    def to_document(self) -> dict[str, object]:
        return {"approved": self.approved, "reasons": list(self.reasons)}


def decide_model_activation(
    candidate: TemporalEvaluationResult,
    *,
    incumbent_metrics: dict[str, object] | None = None,
    gate: ActivationQualityGate | None = None,
) -> ActivationDecision:
    resolved_gate = gate or ActivationQualityGate()
    reasons: list[str] = []
    if candidate.users < resolved_gate.min_evaluation_users:
        reasons.append(
            f"evaluation users {candidate.users} < {resolved_gate.min_evaluation_users}"
        )
    if candidate.catalog_coverage < resolved_gate.min_catalog_coverage:
        reasons.append(
            "catalog coverage "
            f"{candidate.catalog_coverage:.4f} < {resolved_gate.min_catalog_coverage:.4f}"
        )
    if candidate.build.stats.artifacts <= 0:
        reasons.append("candidate has no artifacts")

    incumbent_ndcg = (
        incumbent_metrics.get("ndcgAt10") if incumbent_metrics is not None else None
    )
    candidate_ndcg = candidate.metrics.get("ndcgAt10")
    if isinstance(incumbent_ndcg, int | float) and candidate_ndcg is not None:
        minimum = float(incumbent_ndcg) - resolved_gate.max_ndcg_at_10_regression
        if candidate_ndcg < minimum:
            reasons.append(f"ndcgAt10 {candidate_ndcg:.4f} < quality floor {minimum:.4f}")
    return ActivationDecision(approved=not reasons, reasons=tuple(reasons))
