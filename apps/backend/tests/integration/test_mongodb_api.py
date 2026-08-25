from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import FastAPI

from app.core.config import Settings
from app.core.security import TokenType, create_token
from app.domain.common import utc_now
from app.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
async def real_mongodb_app() -> AsyncIterator[tuple[FastAPI, Settings]]:
    mongodb_uri = os.getenv("TEST_MONGODB_URI")
    if not mongodb_uri:
        pytest.skip("TEST_MONGODB_URI is not configured")

    suffix = uuid4().hex
    settings = Settings(
        _env_file=None,
        app_env="test",
        mongodb_uri=mongodb_uri,
        redis_url=os.getenv("TEST_REDIS_URL", "redis://localhost:6379/0"),
        mongodb_users_database=f"test_users_{suffix}",
        mongodb_products_database=f"test_products_{suffix}",
        mongodb_orders_database=f"test_orders_{suffix}",
        jwt_secret="integration-test-secret-0123456789abcdef",
        recommendation_user_ref_secret=(
            "integration-recommendation-ref-secret-0123456789abcdef"
        ),
        cookie_secure=False,
        log_json=False,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        try:
            yield app, settings
        finally:
            client = app.state.db._require_client()
            await client.drop_database(settings.mongodb_users_database)
            await client.drop_database(settings.mongodb_products_database)
            await client.drop_database(settings.mongodb_orders_database)


@pytest.mark.asyncio
async def test_real_mongodb_order_flow_is_isolated_and_idempotent(
    real_mongodb_app: tuple[FastAPI, Settings],
) -> None:
    app, settings = real_mongodb_app
    db: Any = app.state.db
    customer_id = ObjectId()
    other_customer_id = ObjectId()
    product_id = ObjectId()
    now = utc_now()
    await db.users.insert_many(
        [
            {
                "_id": customer_id,
                "display_name": "Integration customer",
                "role": "customer",
                "active": True,
                "createdAt": now,
            },
            {
                "_id": other_customer_id,
                "display_name": "Other customer",
                "role": "customer",
                "active": True,
                "createdAt": now,
            },
        ]
    )
    await db.products.insert_one(
        {
            "_id": product_id,
            "productName": "Integration product",
            "price": Decimal128("50.00"),
            "status": "available",
            "addons": [
                {
                    "id": "egg",
                    "name": "Egg",
                    "price": Decimal128("10.00"),
                    "available": True,
                }
            ],
            "createdAt": now,
        }
    )
    customer_token = create_token(
        subject=str(customer_id),
        token_type=TokenType.ACCESS,
        settings=settings,
        lifetime=timedelta(minutes=5),
    )
    other_token = create_token(
        subject=str(other_customer_id),
        token_type=TokenType.ACCESS,
        settings=settings,
        lifetime=timedelta(minutes=5),
    )
    payload = {
        "items": [
            {
                "product_id": str(product_id),
                "quantity": 2,
                "addon_ids": ["egg"],
            }
        ]
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/api/v1/orders",
            headers={
                "Authorization": f"Bearer {customer_token}",
                "Idempotency-Key": "integration-order-123",
            },
            json=payload,
        )
        duplicate = await client.post(
            "/api/v1/orders",
            headers={
                "Authorization": f"Bearer {customer_token}",
                "Idempotency-Key": "integration-order-123",
            },
            json=payload,
        )
        cross_user = await client.get(
            f"/api/v1/orders/{first.json()['id']}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        recommendations = await client.get(
            "/api/v1/recommendations?limit=3",
            headers={"Authorization": f"Bearer {customer_token}"},
        )
        recommendation_event = {
            "event_type": "click",
            "product_id": str(product_id),
            "recommendation_id": recommendations.json()["recommendation_id"],
        }
        first_event = await client.post(
            "/api/v1/recommendations/events",
            headers={"Authorization": f"Bearer {customer_token}"},
            json=recommendation_event,
        )
        cross_user_event = await client.post(
            "/api/v1/recommendations/events",
            headers={"Authorization": f"Bearer {other_token}"},
            json=recommendation_event,
        )
        duplicate_event = await client.post(
            "/api/v1/recommendations/events",
            headers={"Authorization": f"Bearer {customer_token}"},
            json=recommendation_event,
        )

    assert first.status_code == 201
    assert first.json()["total"] == 120.0
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == first.json()["id"]
    assert cross_user.status_code == 404
    assert recommendations.status_code == 200
    assert recommendations.json()["products"][0]["id"] == str(product_id)
    assert first_event.status_code == 202
    assert first_event.json()["duplicate"] is False
    assert duplicate_event.status_code == 202
    assert duplicate_event.json()["duplicate"] is True
    assert cross_user_event.status_code == 404
    assert await db.orders.count_documents({}) == 1
    assert await db.recommendation_events.count_documents({}) == 1
    assert await db.recommendation_slates.count_documents({}) == 1
    stored_event = await db.recommendation_events.find_one({})
    assert stored_event is not None
    assert stored_event["rank"] == 1
    assert stored_event["strategy"] == recommendations.json()["strategy"]
    assert "weight" not in stored_event
    assert "eventId" not in stored_event
    order_indexes = await db.orders.index_information()
    oauth_indexes = await db.oauth_states.index_information()
    recommendation_indexes = await db.recommendation_events.index_information()
    slate_indexes = await db.recommendation_slates.index_information()
    assert "uniq_order_idempotency" in order_indexes
    assert oauth_indexes["ttl_oauth_state"]["expireAfterSeconds"] == 0
    assert "uniq_recommendation_event_dedupe" in recommendation_indexes
    assert slate_indexes["ttl_recommendation_slate"]["expireAfterSeconds"] == 0

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        purge = await client.delete(
            "/api/v1/recommendations/data",
            headers={"Authorization": f"Bearer {customer_token}"},
        )

    assert purge.status_code == 204
    assert await db.recommendation_events.count_documents({}) == 0
    assert await db.recommendation_slates.count_documents({}) == 0
    assert await db.recommendation_event_counters.count_documents({}) == 0
