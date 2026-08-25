from __future__ import annotations

import logging
from datetime import timedelta

from pymongo.errors import DuplicateKeyError

from app.db.mongodb import MongoDatabase
from app.domain.common import utc_now

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_HOURS = 24


class IdempotencyService:
    """Records completed background commands for duplicate suppression.

    A completion marker is deliberately written only *after* the handler
    succeeds. Writing a durable claim before execution can lose work if the
    worker process dies after the claim but before the side effect. Under
    at-least-once delivery, a rare concurrent duplicate is safer than silently
    dropping the only retry. Destructive business operations still receive the
    same idempotency key and enforce their own idempotency at the service layer.
    """

    def __init__(
        self,
        db: MongoDatabase,
        *,
        retention_hours: int = DEFAULT_RETENTION_HOURS,
    ) -> None:
        self.db = db
        self.retention_hours = retention_hours

    async def is_completed(self, *, scope: str, key: str) -> bool:
        """Return whether this logical command completed successfully before."""

        document = await self.db.job_idempotency.find_one(
            {"scope": scope, "key": key},
            projection={"_id": 1},
        )
        return document is not None

    async def mark_completed(self, *, scope: str, key: str) -> None:
        """Record successful completion; concurrent completions are harmless."""

        now = utc_now()
        try:
            await self.db.job_idempotency.insert_one(
                {
                    "scope": scope,
                    "key": key,
                    "completedAt": now,
                    "expiresAt": now + timedelta(hours=self.retention_hours),
                }
            )
        except DuplicateKeyError:
            logger.info("idempotent_completion_already_recorded", extra={"task_name": scope})
