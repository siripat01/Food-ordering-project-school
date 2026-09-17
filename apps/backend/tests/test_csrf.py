from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.middleware import is_trusted_cookie_request
from app.main import create_app


@pytest.mark.parametrize(
    ("method", "cookies", "origin", "referer", "expected"),
    [
        ("GET", {"access_token": "token"}, None, None, True),
        ("POST", {}, None, None, True),
        ("POST", {"access_token": "token"}, "http://localhost:3000", None, True),
        ("PATCH", {"refresh_token": "token"}, None, "http://localhost:3000/page", True),
        ("DELETE", {"access_token": "token"}, "https://evil.example", None, False),
        ("POST", {"access_token": "token"}, None, None, False),
    ],
)
def test_cookie_authenticated_mutations_require_a_trusted_origin(
    method: str,
    cookies: dict[str, str],
    origin: str | None,
    referer: str | None,
    expected: bool,
) -> None:
    assert (
        is_trusted_cookie_request(
            method=method,
            cookies=cookies,
            origin=origin,
            referer=referer,
            allowed_origins={"http://localhost:3000"},
        )
        is expected
    )


@pytest.mark.asyncio
async def test_app_rejects_cookie_mutation_without_browser_origin(settings) -> None:
    app = create_app(settings, initialize_clients=False)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://localhost:8000",
    ) as client:
        client.cookies.set("refresh_token", "refresh-token")
        response = await client.post("/api/v1/auth/refresh")

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}
