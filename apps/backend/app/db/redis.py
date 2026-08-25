from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import Settings


class RedisDatabase:
    """Lifecycle wrapper for the shared Redis connection pool."""

    def __init__(self, settings: Settings) -> None:
        self.client = Redis.from_url(str(settings.redis_url), decode_responses=True)

    async def connect(self) -> None:
        await self.client.ping()

    async def ping(self) -> bool:
        try:
            return bool(await self.client.ping())
        except Exception:
            return False

    async def close(self) -> None:
        await self.client.aclose()
