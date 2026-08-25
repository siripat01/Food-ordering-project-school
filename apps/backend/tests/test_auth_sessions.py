from __future__ import annotations

import pytest
from fakes import FakeRedis

from app.core.security import TokenType, decode_token
from app.services.auth_sessions import AuthSessionService


@pytest.mark.asyncio
async def test_refresh_rotation_revokes_the_previous_access_token(settings) -> None:
    sessions = AuthSessionService(FakeRedis(), settings)
    access, refresh = await sessions.issue("customer-1")
    access_claims = decode_token(access, expected_type=TokenType.ACCESS, settings=settings)

    assert await sessions.access_is_active(access_claims)

    rotated = await sessions.rotate(refresh)

    assert rotated is not None
    new_access, new_refresh = rotated
    new_access_claims = decode_token(
        new_access, expected_type=TokenType.ACCESS, settings=settings
    )
    assert not await sessions.access_is_active(access_claims)
    assert await sessions.access_is_active(new_access_claims)
    assert await sessions.rotate(refresh) is None
    assert await sessions.rotate(new_refresh) is not None
