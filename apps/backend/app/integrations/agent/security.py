from __future__ import annotations

import json
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from redis.asyncio import Redis

CONFIRMATION_COMMANDS = frozenset({"ยืนยัน", "ยืนยันรายการ", "confirm"})
CANCELLATION_COMMANDS = frozenset({"ยกเลิก", "ยกเลิกรายการ", "cancel"})


def normalized_command(message: str) -> str:
    return " ".join(message.strip().casefold().split())


@dataclass(frozen=True, slots=True)
class PendingAction:
    tool_name: str
    arguments: dict[str, Any]
    idempotency_key: str
    expires_at: datetime


class PendingActionStore:
    """Redis-backed, short-lived confirmation state keyed by authenticated user ID."""

    def __init__(self, redis: Redis, *, ttl_minutes: int) -> None:
        self.redis = redis
        self.ttl_seconds = ttl_minutes * 60

    @staticmethod
    def _key(user_id: str) -> str:
        return f"agent:pending-action:{user_id}"

    async def put(
        self,
        *,
        user_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        idempotency_key: str,
    ) -> PendingAction:
        expires_at = datetime.now(UTC) + timedelta(seconds=self.ttl_seconds)
        action = PendingAction(tool_name, arguments, idempotency_key, expires_at)
        await self.redis.set(
            self._key(user_id),
            json.dumps(
                {
                    "toolName": tool_name,
                    "arguments": arguments,
                    "idempotencyKey": idempotency_key,
                    "expiresAt": expires_at.isoformat(),
                },
                separators=(",", ":"),
            ),
            ex=self.ttl_seconds,
        )
        return action

    async def get(self, user_id: str) -> PendingAction | None:
        return self._deserialize(await self.redis.get(self._key(user_id)))

    async def consume(self, user_id: str) -> PendingAction | None:
        return self._deserialize(await self.redis.getdel(self._key(user_id)))

    async def clear(self, user_id: str) -> None:
        await self.redis.delete(self._key(user_id))

    @staticmethod
    def _deserialize(raw: str | bytes | None) -> PendingAction | None:
        if raw is None:
            return None
        try:
            value = json.loads(raw)
            expires_at = datetime.fromisoformat(value["expiresAt"])
            if expires_at <= datetime.now(UTC):
                return None
            arguments = value["arguments"]
            if not isinstance(arguments, dict):
                return None
            return PendingAction(
                tool_name=str(value["toolName"]),
                arguments=arguments,
                idempotency_key=str(value["idempotencyKey"]),
                expires_at=expires_at,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None


class PerUserRateLimiter:
    """Redis sliding-window limiter shared by all backend processes."""

    _ALLOW_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local cutoff = now - 60
local limit = tonumber(ARGV[2])
local member = ARGV[3]
redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
if redis.call('ZCARD', key) >= limit then return 0 end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, 61)
return 1
"""

    def __init__(self, redis: Redis, *, requests_per_minute: int) -> None:
        self.redis = redis
        self.limit = requests_per_minute

    async def allow(self, user_id: str) -> bool:
        allowed = await cast(
            Awaitable[Any],
            self.redis.eval(
                self._ALLOW_SCRIPT,
                1,
                f"agent:rate-limit:{user_id}",
                str(datetime.now(UTC).timestamp()),
                str(self.limit),
                uuid4().hex,
            ),
        )
        return bool(allowed)
