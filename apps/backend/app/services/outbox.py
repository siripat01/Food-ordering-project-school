from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from pymongo import ASCENDING, ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import DuplicateKeyError

from app.db.mongodb import MongoDatabase
from app.domain.common import parse_object_id, serialize_mongo, utc_now
from app.domain.outbox import (
    CLAIM_VISIBILITY_TIMEOUT_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    OutboxEvent,
    OutboxStatus,
    next_available_at,
)

logger = logging.getLogger(__name__)

_MAX_ERROR_LENGTH = 500

CLAIMABLE_STATUSES = [
    OutboxStatus.PENDING.value,
    OutboxStatus.PROCESSING.value,
    OutboxStatus.FAILED.value,
]


class OutboxRepository:
    """Mongo access for the transactional outbox collection.

    The repository deliberately knows nothing about Taskiq. It only stores and
    transitions committed facts; turning them into background commands is the
    dispatcher's job.
    """

    def __init__(self, db: MongoDatabase) -> None:
        self.db = db

    async def insert(
        self,
        document: dict[str, Any],
        *,
        session: AsyncClientSession | None = None,
    ) -> str:
        result = await self.db.outbox_events.insert_one(document, session=session)
        return str(result.inserted_id)

    async def claim_one(self) -> dict[str, Any] | None:
        """Atomically move one due event into ``processing``.

        A single ``find_one_and_update`` is the claim, so two dispatchers racing
        on the same event can never both win. ``availableAt`` doubles as a
        visibility deadline: an event left in ``processing`` by a crashed
        dispatcher becomes claimable again once the deadline passes, and a
        ``failed`` event becomes claimable again once its backoff elapses.
        Terminal statuses (``sent``, ``dead``) are never claimed.
        """
        now = utc_now()
        return await self.db.outbox_events.find_one_and_update(
            {
                "status": {"$in": CLAIMABLE_STATUSES},
                "availableAt": {"$lte": now},
            },
            {
                "$set": {
                    "status": OutboxStatus.PROCESSING.value,
                    "claimedAt": now,
                    "availableAt": now + timedelta(seconds=CLAIM_VISIBILITY_TIMEOUT_SECONDS),
                },
                "$inc": {"attempts": 1},
            },
            sort=[("availableAt", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )

    async def update_status(self, event_id: str, changes: dict[str, Any]) -> None:
        await self.db.outbox_events.update_one(
            {"_id": parse_object_id(event_id)},
            {"$set": changes},
        )

    async def find_one(self, event_id: str) -> dict[str, Any] | None:
        return await self.db.outbox_events.find_one({"_id": parse_object_id(event_id)})


class OutboxService:
    """Writes committed facts to the outbox and owns their delivery lifecycle."""

    def __init__(
        self,
        repository: OutboxRepository,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self.repository = repository
        self.max_attempts = max_attempts

    async def save_event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        session: AsyncClientSession | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> str | None:
        """Persist one fact.

        Must be called with the same ``session`` as the business write it
        belongs to, so the fact and the state it describes commit together.
        Returns ``None`` when ``idempotency_key`` was already recorded.
        """
        now = utc_now()
        document = serialize_mongo(
            {
                "eventType": event_type,
                "payload": payload,
                "status": OutboxStatus.PENDING.value,
                "createdAt": now,
                "publishedAt": None,
                "attempts": 0,
                "maxAttempts": self.max_attempts,
                "availableAt": now,
                "lastError": None,
                "correlationId": correlation_id,
                "idempotencyKey": idempotency_key,
            }
        )
        try:
            event_id = await self.repository.insert(document, session=session)
        except DuplicateKeyError:
            logger.info(
                "outbox_event_deduplicated",
                extra={"event_type": event_type, "correlation_id": correlation_id},
            )
            return None
        logger.info(
            "outbox_event_saved",
            extra={
                "event_id": event_id,
                "event_type": event_type,
                "correlation_id": correlation_id,
            },
        )
        return event_id

    async def claim_pending_events(self, *, limit: int = 20) -> list[OutboxEvent]:
        """Claim up to ``limit`` due events for this dispatcher instance."""
        claimed: list[OutboxEvent] = []
        for _ in range(max(limit, 0)):
            document = await self.repository.claim_one()
            if document is None:
                break
            claimed.append(OutboxEvent.from_document(document))
        return claimed

    async def mark_as_sent(self, event: OutboxEvent) -> None:
        await self.repository.update_status(
            event.id,
            {
                "status": OutboxStatus.SENT.value,
                "publishedAt": utc_now(),
                "lastError": None,
            },
        )

    async def mark_as_failed(self, event: OutboxEvent, *, error: str) -> OutboxStatus:
        """Schedule a retry, or give up once ``max_attempts`` is reached.

        The payload is never cleared, so a dead event stays fully inspectable.
        """
        if event.attempts >= event.max_attempts:
            await self.mark_as_dead(event, error=error)
            return OutboxStatus.DEAD
        await self.repository.update_status(
            event.id,
            {
                "status": OutboxStatus.FAILED.value,
                "lastError": error[:_MAX_ERROR_LENGTH],
                "availableAt": next_available_at(event.attempts),
            },
        )
        logger.warning(
            "outbox_event_retry_scheduled",
            extra={
                "attempt": event.attempts,
                "event_id": event.id,
                "event_type": event.event_type,
                "correlation_id": event.correlation_id,
            },
        )
        return OutboxStatus.FAILED

    async def mark_as_dead(self, event: OutboxEvent, *, error: str) -> None:
        await self.repository.update_status(
            event.id,
            {
                "status": OutboxStatus.DEAD.value,
                "lastError": error[:_MAX_ERROR_LENGTH],
            },
        )
        logger.error(
            "outbox_event_dead",
            extra={
                "attempt": event.attempts,
                "event_id": event.id,
                "event_type": event.event_type,
                "correlation_id": event.correlation_id,
            },
        )
