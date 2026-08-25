from __future__ import annotations

import logging
from datetime import timedelta

from pymongo.errors import DuplicateKeyError

from app.db.mongodb import MongoDatabase
from app.domain.common import utc_now

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_HOURS = 24


class IdempotencyService:
    """Suppresses duplicate side effects under at-least-once delivery.

    Redis Streams and the outbox both guarantee *at least* once, never exactly
    once, so any handler with an external side effect claims a key first and
    releases it if the work did not actually complete.
    """

    def __init__(
        self,
        db: MongoDatabase,
        *,
        retention_hours: int = DEFAULT_RETENTION_HOURS,
    ) -> None:
        self.db = db
        self.retention_hours = retention_hours

    async def claim(self, *, scope: str, key: str) -> bool:
        """Return ``True`` when this caller is the first to claim ``key``."""
        now = utc_now()
        try:
            await self.db.job_idempotency.insert_one(
                {
                    "scope": scope,
                    "key": key,
                    "createdAt": now,
                    "expiresAt": now + timedelta(hours=self.retention_hours),
                }
            )
        except DuplicateKeyError:
            logger.info("idempotent_replay_skipped", extra={"task_name": scope})
            return False
        return True

    async def release(self, *, scope: str, key: str) -> None:
        """Undo a claim so a failed attempt can be retried."""
        await self.db.job_idempotency.delete_one({"scope": scope, "key": key})
