from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo import AsyncMongoClient

from app.core.config import Settings
from app.services.recommendation_models import (
    ModelBuildConfig,
    ModelBuildLimitError,
    RecommendationInteraction,
    evaluate_temporal_split,
    ndcg_at_k,
    recall_at_k,
)
from scripts.build_recommendation_model import (
    iter_completed_order_interactions,
    load_catalog_product_ids,
    load_recent_product_ids,
)

__all__ = ["evaluate_leave_one_out", "ndcg_at_k", "recall_at_k"]


def evaluate_leave_one_out(events: list[dict[str, Any]], *, k: int) -> dict[str, Any]:
    """Backward-compatible small-fixture popularity check.

    Production evaluation uses the global temporal split in `run`; this helper remains for
    existing deterministic unit fixtures and must not be used for model activation.
    """

    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        user_ref = event.get("userRef")
        product_id = event.get("productId")
        if isinstance(user_ref, str) and isinstance(product_id, str):
            by_user[user_ref].append(event)

    train_counts: Counter[str] = Counter()
    held_out: dict[str, str] = {}
    for user_ref, user_events in by_user.items():
        ordered = sorted(
            user_events,
            key=lambda item: item.get("createdAt") or datetime.now(UTC),
        )
        if len(ordered) < 2:
            continue
        held_out[user_ref] = str(ordered[-1]["productId"])
        for event in ordered[:-1]:
            train_counts[str(event["productId"])] += int(event.get("quantity", 1))

    ranking = [product_id for product_id, _count in train_counts.most_common()]
    if not held_out:
        return {"users": 0, "k": k, "recallAtK": 0.0, "ndcgAtK": 0.0}
    recalls = [recall_at_k(ranking, {product_id}, k) for product_id in held_out.values()]
    ndcgs = [ndcg_at_k(ranking, {product_id}, k) for product_id in held_out.values()]
    return {
        "users": len(held_out),
        "k": k,
        "recallAtK": sum(recalls) / len(recalls),
        "ndcgAtK": sum(ndcgs) / len(ndcgs),
    }


async def load_purchase_interactions(
    orders: Any,
    *,
    start: datetime,
    end: datetime,
    max_interactions: int,
    batch_size: int,
    user_ref_key: bytes,
) -> list[RecommendationInteraction]:
    interactions: list[RecommendationInteraction] = []
    async for interaction in iter_completed_order_interactions(
        orders,
        start=start,
        end=end,
        batch_size=batch_size,
        user_ref_key=user_ref_key,
    ):
        if len(interactions) >= max_interactions:
            raise ModelBuildLimitError(
                f"Evaluation source exceeds max_interactions={max_interactions}"
            )
        interactions.append(interaction)
    return interactions


async def run(
    *,
    days: int,
    test_days: int = 14,
    max_interactions: int = 250_000,
    batch_size: int = 500,
    min_support: int = 3,
    top_neighbors: int = 50,
    k: int | None = None,
) -> dict[str, Any]:
    settings = Settings()
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        settings.mongodb_uri.get_secret_value(),
        serverSelectionTimeoutMS=5_000,
        tz_aware=True,
        appname="food-ordering-recommendation-evaluation",
    )
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=test_days)
    config = ModelBuildConfig(
        training_window_days=days,
        max_interactions=max_interactions,
        min_pair_support=min_support,
        top_neighbors=top_neighbors,
    )
    ks = tuple(sorted({5, 10, *([k] if k else [])}))
    try:
        await client.admin.command("ping")
        orders_database = client[settings.mongodb_orders_database]
        products = client[settings.mongodb_products_database]["products"]
        catalog = await load_catalog_product_ids(
            products,
            max_products=config.max_catalog_products,
        )
        recent_product_ids = await load_recent_product_ids(
            products,
            max_products=config.max_catalog_products,
            created_before=cutoff,
        )
        interactions = await load_purchase_interactions(
            orders_database["orders"],
            start=cutoff - timedelta(days=days),
            end=now,
            max_interactions=max_interactions,
            batch_size=batch_size,
            user_ref_key=(settings.recommendation_user_ref_secret.get_secret_value().encode()),
        )
        trending = evaluate_temporal_split(
            interactions,
            catalog_product_ids=catalog,
            cutoff=cutoff,
            config=config,
            algorithm="trending",
            ks=ks,
        )
        item_item = evaluate_temporal_split(
            interactions,
            catalog_product_ids=catalog,
            cutoff=cutoff,
            config=config,
            algorithm="item_item",
            ks=ks,
        )
        recent = evaluate_temporal_split(
            interactions,
            catalog_product_ids=catalog,
            cutoff=cutoff,
            config=config,
            algorithm="recent",
            ks=ks,
            recent_product_ids=recent_product_ids,
        )
        return {
            "windowDays": days,
            "testDays": test_days,
            "sourceInteractions": len(interactions),
            "catalogProducts": len(catalog),
            "trending": trending.to_document(),
            "itemItem": item_item.to_document(),
            "recent": recent.to_document(),
            "comparison": {
                "itemItemMinusTrending": {
                    f"ndcgAt{metric_k}Delta": (
                        item_item.metrics[f"ndcgAt{metric_k}"]
                        - trending.metrics[f"ndcgAt{metric_k}"]
                    )
                    for metric_k in ks
                },
                "itemItemMinusRecent": {
                    f"ndcgAt{metric_k}Delta": (
                        item_item.metrics[f"ndcgAt{metric_k}"]
                        - recent.metrics[f"ndcgAt{metric_k}"]
                    )
                    for metric_k in ks
                },
            },
        }
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate CPU recommendation models with a leakage-free temporal split."
    )
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--test-days", type=int, default=14)
    parser.add_argument("--max-interactions", type=int, default=250_000)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--min-support", type=int, default=3)
    parser.add_argument("--top-neighbors", type=int, default=50)
    parser.add_argument("--k", type=int)
    args = parser.parse_args()
    positive = (
        args.days,
        args.test_days,
        args.max_interactions,
        args.batch_size,
        args.min_support,
        args.top_neighbors,
    )
    if any(value <= 0 for value in positive) or (args.k is not None and args.k <= 0):
        parser.error("All evaluation windows, limits and K values must be positive")
    result = asyncio.run(
        run(
            days=args.days,
            test_days=args.test_days,
            max_interactions=args.max_interactions,
            batch_size=args.batch_size,
            min_support=args.min_support,
            top_neighbors=args.top_neighbors,
            k=args.k,
        )
    )
    print(json.dumps(result, default=str, separators=(",", ":")))


if __name__ == "__main__":
    main()
