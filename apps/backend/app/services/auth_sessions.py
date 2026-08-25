from __future__ import annotations

import hashlib
from collections.abc import Awaitable
from datetime import timedelta
from typing import Any, cast
from uuid import uuid4

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.security import TokenType, create_token, decode_token


class AuthSessionService:
    """Redis-backed access/refresh token sessions with one-time refresh rotation."""

    def __init__(self, redis: Redis, settings: Settings) -> None:
        self.redis = redis
        self.settings = settings

    @staticmethod
    def _session_key(session_id: str) -> str:
        return f"auth:session:{session_id}"

    @staticmethod
    def _refresh_key(token_id: str) -> str:
        return f"auth:refresh:{token_id}"

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    async def issue(self, user_id: str, *, session_id: str | None = None) -> tuple[str, str]:
        session_id = session_id or uuid4().hex
        access = create_token(
            subject=user_id,
            token_type=TokenType.ACCESS,
            settings=self.settings,
            lifetime=timedelta(minutes=self.settings.access_token_ttl_minutes),
            extra={"sid": session_id},
        )
        refresh = create_token(
            subject=user_id,
            token_type=TokenType.REFRESH,
            settings=self.settings,
            lifetime=timedelta(days=self.settings.refresh_token_ttl_days),
            extra={"sid": session_id},
        )
        access_claims = decode_token(
            access, expected_type=TokenType.ACCESS, settings=self.settings
        )
        refresh_claims = decode_token(
            refresh, expected_type=TokenType.REFRESH, settings=self.settings
        )
        ttl = self.settings.refresh_token_ttl_days * 24 * 60 * 60
        session_key = self._session_key(session_id)
        await cast(
            Awaitable[Any],
            self.redis.hset(
                session_key,
                mapping={"userId": user_id, "accessJti": str(access_claims["jti"])},
            ),
        )
        await self.redis.expire(session_key, ttl)
        await self.redis.set(
            self._refresh_key(str(refresh_claims["jti"])), self._digest(refresh), ex=ttl
        )
        return access, refresh

    async def access_is_active(self, claims: dict[str, Any]) -> bool:
        session_id = claims.get("sid")
        if not isinstance(session_id, str):
            return False
        session = await cast(
            Awaitable[dict[Any, Any]], self.redis.hgetall(self._session_key(session_id))
        )
        return (
            bool(session)
            and session.get("userId") == claims.get("sub")
            and session.get("accessJti") == claims.get("jti")
        )

    async def rotate(self, refresh_token: str) -> tuple[str, str] | None:
        claims = decode_token(
            refresh_token, expected_type=TokenType.REFRESH, settings=self.settings
        )
        session_id = claims.get("sid")
        if not isinstance(session_id, str):
            return None
        stored = await self.redis.getdel(self._refresh_key(str(claims["jti"])))
        if stored != self._digest(refresh_token):
            return None
        session = await cast(
            Awaitable[dict[Any, Any]], self.redis.hgetall(self._session_key(session_id))
        )
        if not session or session.get("userId") != claims.get("sub"):
            return None
        return await self.issue(str(claims["sub"]), session_id=session_id)

    async def revoke(self, claims: dict[str, Any]) -> None:
        session_id = claims.get("sid")
        if isinstance(session_id, str):
            await self.redis.delete(self._session_key(session_id))
