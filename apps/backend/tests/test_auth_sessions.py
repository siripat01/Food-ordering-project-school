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


@pytest.mark.asyncio
async def test_refresh_is_not_consumed_when_session_state_is_missing(settings) -> None:
    redis = FakeRedis()
    sessions = AuthSessionService(redis, settings)
    _access, refresh = await sessions.issue("customer-1")
    claims = decode_token(refresh, expected_type=TokenType.REFRESH, settings=settings)
    refresh_key = sessions._refresh_key(str(claims["jti"]))
    stored_digest = redis.values[refresh_key]

    await redis.delete(sessions._session_key(str(claims["sid"])))

    assert await sessions.rotate(refresh) is None
    assert redis.values[refresh_key] == stored_digest


@pytest.mark.asyncio
async def test_refresh_is_not_consumed_when_session_user_does_not_match(settings) -> None:
    redis = FakeRedis()
    sessions = AuthSessionService(redis, settings)
    _access, refresh = await sessions.issue("customer-1")
    claims = decode_token(refresh, expected_type=TokenType.REFRESH, settings=settings)
    refresh_key = sessions._refresh_key(str(claims["jti"]))
    session_key = sessions._session_key(str(claims["sid"]))
    stored_digest = redis.values[refresh_key]
    redis.hashes[session_key]["userId"] = "another-user"

    assert await sessions.rotate(refresh) is None
    assert redis.values[refresh_key] == stored_digest
