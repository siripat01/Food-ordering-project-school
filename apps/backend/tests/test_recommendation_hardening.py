from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from bson import ObjectId
from pydantic import SecretStr, ValidationError
from pymongo.errors import DuplicateKeyError

from app.core.config import Settings
from app.db.mongodb import MongoDatabase
from app.domain.errors import ConflictError, NotFoundError
from app.domain.orders import (
    OrderItemResponse,
    OrderResponse,
    OrderStatus,
    StatusHistoryEntry,
)
from app.domain.products import ProductListResponse, ProductResponse, ProductStatus
from app.domain.recommendations import RecommendationEventCreate, RecommendationStrategy
from app.services.recommendations import RecommendationService


def recommendation_settings(
    *,
    ref_secret: str = "recommendation-ref-secret-0123456789abcdef",  # noqa: S107
    jwt_secret: str = "unrelated-jwt-secret-0123456789abcdef",  # noqa: S107
) -> SimpleNamespace:
    return SimpleNamespace(
        jwt_secret=SecretStr(jwt_secret),
        recommendation_user_ref_secret=SecretStr(ref_secret),
        recommendation_user_ref_key_version="v1",
        recommendation_user_ref_previous_secrets={},
        recommendation_slate_retention_days=7,
        recommendation_daily_impression_cap=10,
        recommendation_daily_click_cap=5,
        recommendation_daily_add_to_cart_cap=3,
        recommender_enabled=False,
        recommender_mode="local",
        recommender_url=None,
        recommender_timeout_seconds=3,
    )


def recommendation_service(
    *,
    settings: SimpleNamespace | None = None,
) -> tuple[RecommendationService, SimpleNamespace, AsyncMock]:
    events = AsyncMock()
    events.find_one.return_value = None
    slates = AsyncMock()
    counters = AsyncMock()
    counters.find_one_and_update.return_value = {"count": 1}
    db = SimpleNamespace(
        recommendation_events=events,
        recommendation_slates=slates,
        recommendation_event_counters=counters,
    )
    products = AsyncMock()
    service = RecommendationService(
        db=db,
        products=products,
        settings=settings or recommendation_settings(),
        http_client=AsyncMock(),
    )
    return service, db, products


def valid_slate(product_id: str) -> dict[str, object]:
    return {
        "_id": "recommendation-slate-123",
        "items": [{"productId": product_id, "rank": 4}],
        "placement": "customer-home",
        "strategy": "popularity",
        "modelVersion": "baseline-v1",
    }


def client_event(product_id: str) -> RecommendationEventCreate:
    return RecommendationEventCreate(
        event_type="click",
        product_id=product_id,
        recommendation_id="recommendation-slate-123",
    )


def completed_order(*, product_id: str, status: OrderStatus) -> OrderResponse:
    now = datetime.now(UTC)
    item = OrderItemResponse(
        product_id=product_id,
        product_name_snapshot="Test product",
        unit_price=Decimal("25"),
        quantity=2,
        line_total=Decimal("50"),
    )
    duplicate = item.model_copy(update={"quantity": 3, "line_total": Decimal("75")})
    return OrderResponse(
        id=str(ObjectId()),
        user_id=str(ObjectId()),
        items=[item, duplicate],
        subtotal=Decimal("125"),
        total=Decimal("125"),
        status=status,
        status_history=[StatusHistoryEntry(status=status, changed_at=now)],
        created_at=now,
        updated_at=now,
        completed_at=now if status is OrderStatus.COMPLETED else None,
        cancelled_at=now if status is OrderStatus.CANCELLED else None,
    )


def product(product_id: str) -> ProductResponse:
    return ProductResponse(
        id=product_id,
        name="Test product",
        price=Decimal("25"),
        status=ProductStatus.AVAILABLE,
    )


def test_recommendation_secret_is_required_and_rejects_placeholders() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            mongodb_uri="mongodb://localhost:27017",
            jwt_secret="valid-jwt-secret-0123456789abcdef",
            recommendation_user_ref_secret="replace-with-recommendation-secret-0123456789",
        )


def test_user_ref_does_not_change_when_jwt_secret_rotates() -> None:
    first, _, _ = recommendation_service(
        settings=recommendation_settings(jwt_secret="first-jwt-secret-0123456789abcdef")
    )
    rotated, _, _ = recommendation_service(
        settings=recommendation_settings(jwt_secret="rotated-jwt-secret-0123456789abcdef")
    )

    assert first._user_ref("user-123") == rotated._user_ref("user-123")


@pytest.mark.asyncio
async def test_ttl_index_retention_change_uses_collmod_without_drop() -> None:
    database = SimpleNamespace(command=AsyncMock())
    collection = SimpleNamespace(
        name="recommendation_events",
        database=database,
        index_information=AsyncMock(
            return_value={
                "ttl_recommendation_event": {
                    "key": [("createdAt", 1)],
                    "expireAfterSeconds": 86_400,
                }
            }
        ),
        create_index=AsyncMock(),
    )

    await MongoDatabase._ensure_ttl_index(
        collection,
        field="createdAt",
        expire_after_seconds=172_800,
        name="ttl_recommendation_event",
    )

    collection.create_index.assert_not_awaited()
    database.command.assert_awaited_once_with(
        {
            "collMod": "recommendation_events",
            "index": {
                "name": "ttl_recommendation_event",
                "expireAfterSeconds": 172_800,
            },
        }
    )


@pytest.mark.asyncio
async def test_event_requires_same_user_unexpired_slate_product() -> None:
    product_id = str(ObjectId())
    service, db, products = recommendation_service()
    db.recommendation_slates.find_one.return_value = None

    with pytest.raises(NotFoundError, match="Recommendation slate not found"):
        await service.record_client_event(
            user_id=str(ObjectId()),
            payload=client_event(product_id),
        )

    products.get_available_by_ids.assert_not_awaited()
    db.recommendation_events.insert_one.assert_not_awaited()
    slate_query = db.recommendation_slates.find_one.await_args.args[0]
    assert "userRef" in slate_query
    assert slate_query["expiresAt"] == {"$gt": slate_query["expiresAt"]["$gt"]}
    assert slate_query["items"] == {"$elemMatch": {"productId": product_id}}


@pytest.mark.asyncio
async def test_event_stores_only_server_derived_attribution_and_dedupe() -> None:
    product_id = str(ObjectId())
    service, db, products = recommendation_service()
    db.recommendation_slates.find_one.return_value = valid_slate(product_id)
    products.get_available_by_ids.return_value = [SimpleNamespace(id=product_id)]

    created = await service.record_client_event(
        user_id=str(ObjectId()),
        payload=client_event(product_id),
    )
    inserted = db.recommendation_events.insert_one.await_args.args[0]

    assert created is True
    assert inserted["rank"] == 4
    assert inserted["strategy"] == "popularity"
    assert inserted["modelVersion"] == "baseline-v1"
    assert inserted["placement"] == "customer-home"
    assert inserted["source"] == "served_slate"
    assert len(inserted["dedupeKey"]) == 64
    assert "eventId" not in inserted
    assert "weight" not in inserted
    assert "userId" not in inserted


@pytest.mark.asyncio
async def test_event_daily_cap_is_atomic_and_bounded() -> None:
    product_id = str(ObjectId())
    service, db, products = recommendation_service()
    db.recommendation_slates.find_one.return_value = valid_slate(product_id)
    db.recommendation_event_counters.find_one_and_update.side_effect = DuplicateKeyError(
        "counter reached its cap"
    )
    products.get_available_by_ids.return_value = [SimpleNamespace(id=product_id)]

    with pytest.raises(ConflictError, match="Daily recommendation event limit reached"):
        await service.record_client_event(
            user_id=str(ObjectId()),
            payload=client_event(product_id),
        )

    db.recommendation_events.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_purchase_aggregates_duplicate_lines_and_is_idempotent() -> None:
    product_id = str(ObjectId())
    service, db, _ = recommendation_service()
    db.recommendation_events.find_one.return_value = {
        "recommendationId": "recommendation-slate-123",
        "rank": 2,
        "placement": "customer-home",
        "strategy": "popularity",
        "modelVersion": "baseline-v1",
    }
    order = completed_order(product_id=product_id, status=OrderStatus.COMPLETED)

    await service.record_purchase(order)
    event = db.recommendation_events.insert_one.await_args.args[0]
    db.recommendation_events.insert_one.side_effect = DuplicateKeyError("duplicate purchase")
    await service.record_purchase(order)
    duplicate_event = db.recommendation_events.insert_one.await_args.args[0]

    assert event["quantity"] == 5
    assert event["orderId"] == order.id
    assert event["source"] == "completed_order"
    assert event["recommendationId"] == "recommendation-slate-123"
    assert "weight" not in event
    assert len(event["dedupeKey"]) == 64
    assert duplicate_event["dedupeKey"] == event["dedupeKey"]
    assert db.recommendation_events.insert_one.await_count == 2


@pytest.mark.asyncio
async def test_cancelled_order_never_records_purchase() -> None:
    product_id = str(ObjectId())
    service, db, _ = recommendation_service()

    await service.record_purchase(
        completed_order(product_id=product_id, status=OrderStatus.CANCELLED)
    )

    db.recommendation_events.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_data_purge_covers_previous_key_versions_and_runtime_cache() -> None:
    settings = recommendation_settings()
    settings.recommendation_user_ref_key_version = "v2"
    settings.recommendation_user_ref_previous_secrets = {
        "v1": SecretStr("previous-recommendation-ref-secret-0123456789abcdef")
    }
    service, db, _ = recommendation_service(settings=settings)
    runtime = SimpleNamespace(evict_user=AsyncMock())
    service.runtime = runtime

    await service.purge_user_data(user_id="user-123")

    assert runtime.evict_user.call_count == 2
    queries = [
        db.recommendation_slates.delete_many.await_args.args[0],
        db.recommendation_events.delete_many.await_args.args[0],
        db.recommendation_event_counters.delete_many.await_args.args[0],
    ]
    assert queries[0] == queries[1] == queries[2]
    assert len(queries[0]["userRef"]["$in"]) == 2
    assert all(len(user_ref) == 64 for user_ref in queries[0]["userRef"]["$in"])


@pytest.mark.asyncio
async def test_external_first_mode_does_not_send_direct_user_identity() -> None:
    settings = recommendation_settings()
    settings.recommender_enabled = True
    settings.recommender_mode = "external_first"
    settings.recommender_url = "https://recommender.example.test/recommend"
    product_id = str(ObjectId())
    service, db, products = recommendation_service(settings=settings)
    runtime = AsyncMock()
    service.runtime = runtime
    upstream = Mock()
    upstream.json.return_value = {"product_ids": [product_id, product_id]}
    service.http_client.post.return_value = upstream
    products.get_available_by_ids.return_value = [product(product_id)]

    response = await service.recommend(user_id="private-user-id", limit=1)

    assert response.strategy is RecommendationStrategy.EXTERNAL
    runtime.recommend.assert_not_awaited()
    upstream.raise_for_status.assert_called_once()
    upstream_payload = service.http_client.post.await_args.kwargs["json"]
    assert upstream_payload["user_ref"] != "private-user-id"
    assert len(upstream_payload["user_ref"]) == 64
    products.get_available_by_ids.assert_awaited_once_with([product_id])
    slate = db.recommendation_slates.insert_one.await_args.args[0]
    assert slate["modelVersion"] == "external"


@pytest.mark.asyncio
async def test_external_fallback_runs_only_after_local_model_miss() -> None:
    settings = recommendation_settings()
    settings.recommender_enabled = True
    settings.recommender_mode = "external_fallback"
    settings.recommender_url = "https://recommender.example.test/recommend"
    product_id = str(ObjectId())
    service, _, products = recommendation_service(settings=settings)
    runtime = AsyncMock()
    runtime.recommend.return_value = None
    service.runtime = runtime
    upstream = Mock()
    upstream.json.return_value = {"product_ids": [product_id]}
    service.http_client.post.return_value = upstream
    products.get_available_by_ids.return_value = [product(product_id)]

    response = await service.recommend(user_id="private-user-id", limit=1)

    assert response.strategy is RecommendationStrategy.EXTERNAL
    runtime.recommend.assert_awaited_once()
    service.http_client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_local_runtime_miss_falls_back_to_recent_catalog() -> None:
    settings = recommendation_settings()
    product_id = str(ObjectId())
    service, db, products = recommendation_service(settings=settings)
    runtime = AsyncMock()
    runtime.recommend.return_value = None
    service.runtime = runtime
    products.list_recent_available.return_value = ProductListResponse(
        products=[product(product_id)]
    )

    response = await service.recommend(user_id="private-user-id", limit=1)

    assert response.strategy is RecommendationStrategy.RECENT
    assert response.products[0].id == product_id
    products.list_recent_available.assert_awaited_once_with(limit=1)
    slate = db.recommendation_slates.insert_one.await_args.args[0]
    assert slate["modelVersion"] == "recent"
