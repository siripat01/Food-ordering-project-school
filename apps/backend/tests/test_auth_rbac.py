from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest

from app.api.dependencies import get_current_user
from app.domain.orders import OrderResponse, OrderStatus, StatusHistoryEntry
from app.domain.users import CurrentUser, Role
from app.main import create_app


async def customer(user_id: str = "507f1f77bcf86cd799439011") -> CurrentUser:
    return CurrentUser(
        id=user_id,
        role=Role.CUSTOMER,
        display_name="Test customer",
        active=True,
    )


@pytest.mark.asyncio
async def test_mutating_endpoint_returns_401_without_authentication(settings) -> None:
    app = create_app(settings, initialize_clients=False)
    app.state.users = AsyncMock()
    app.state.products = AsyncMock()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/products",
            json={"name": "Rice", "price": 50, "addons": []},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_customer_cannot_manage_products(settings) -> None:
    app = create_app(settings, initialize_clients=False)
    app.state.products = AsyncMock()
    app.dependency_overrides[get_current_user] = customer
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/products",
            json={"name": "Rice", "price": 50, "addons": []},
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_customer_cannot_read_another_customers_order(settings) -> None:
    app = create_app(settings, initialize_clients=False)
    app.dependency_overrides[get_current_user] = customer
    now = datetime.now(UTC)
    order = OrderResponse(
        id="507f1f77bcf86cd799439012",
        user_id="507f1f77bcf86cd799439099",
        items=[],
        subtotal=Decimal("0"),
        total=Decimal("0"),
        status=OrderStatus.PENDING,
        status_history=[StatusHistoryEntry(status=OrderStatus.PENDING, changed_at=now)],
        created_at=now,
        updated_at=now,
    )
    app.state.orders = AsyncMock()
    app.state.orders.get.return_value = order
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/v1/orders/{order.id}")
    assert response.status_code == 404
