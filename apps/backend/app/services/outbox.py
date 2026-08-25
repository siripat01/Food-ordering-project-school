from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from uuid import uuid4

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
_EXHAUSTED_LEASE_ERROR = "Dispatch lease expired after maximum attempts"

CLAIMABLE_STATUSES = [
    OutboxStatus.PENDING.value,
    OutboxStatus.PROCESSING.value,
    OutboxStatus.FAILED.value,
]


class OutboxLeaseLostError(RuntimeError):
    """Raised when a stale dispatcher tries to mutate an event it no longer owns."""


class OutboxRepository:
    """Mongo access for the transactional outbox collection.

    The repository deliberately knows nothing about Taskiq. It stores facts and
    enforces claim ownership; turning facts into commands is the dispatcher's job.
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
        """Atomically lease one due event to one dispatcher instance.

        ``claimId`` is a fencing token. A dispatcher may update the event only
        while that token still matches, so an expired/stale dispatcher cannot
        overwrite the result of a newer claimant.
        """

        now = utc_now()
        claim_id = uuid4().hex
        return await self.db.outbox_events.find_one_and_update(
            {
                "status": {"$in": CLAIMABLE_STATUSES},
                "availableAt": {"$lte": now},
            },
            {
                "$set": {
                    "status": OutboxStatus.PROCESSING.value,
                    "claimId": claim_id,
                    "claimedAt": now,
                    "availableAt": now + timedelta(seconds=CLAIM_VISIBILITY_TIMEOUT_SECONDS),
                },
                "$inc": {"attempts": 1},
            },
            sort=[("availableAt", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )

    async def update_claimed(
        self,
        event_id: str,
        *,
        claim_id: str,
        changes: dict[str, Any],
    ) -> bool:
        """Update a processing event only if the caller still owns its lease."""

        result = await self.db.outbox_events.update_one(
            {
                "_id": parse_object_id(event_id),
                "status": OutboxStatus.PROCESSING.value,
                "claimId": claim_id,
            },
            {
                "$set": changes,
                "$unset": {
                    "claimId": "",
                    "claimedAt": "",
                },
            },
        )
        return bool(result.matched_count)

    async def find_one(self, event_id: str) -> dict[str, Any] | None:
        return await self.db.outbox_events.find_one({"_id": parse_object_id(event_id)})


class OutboxService:
    """Writes committed facts and owns the outbox delivery lifecycle."""

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
        """Persist one fact in the caller's transaction.

        Returns ``None`` when the same idempotency key was already recorded.
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
        """Claim up to ``limit`` due events.

        A crash consumes an attempt because claiming increments ``attempts``.
        If every previous attempt died without reporting failure, the next
        reclaim fences the exhausted event as ``dead`` instead of dispatching it
        forever.
        """

        claimed: list[OutboxEvent] = []
        for _ in range(max(limit, 0)):
            document = await self.repository.claim_one()
            if document is None:
                break
            event = OutboxEvent.from_document(document)
            if event.attempts > event.max_attempts:
                await self.mark_as_dead(event, error=_EXHAUSTED_LEASE_ERROR)
                continue
            claimed.append(event)
        return claimed

    async def _transition_claimed(
        self,
        event: OutboxEvent,
        *,
        changes: dict[str, Any],
    ) -> None:
        if not event.claim_id:
            raise OutboxLeaseLostError(f"Outbox event {event.id} has no active claim")
        updated = await self.repository.update_claimed(
            event.id,
            claim_id=event.claim_id,
            changes=changes,
        )
        if not updated:
            raise OutboxLeaseLostError(
                f"Outbox event {event.id} is no longer owned by claim {event.claim_id}"
            )

    async def mark_as_sent(self, event: OutboxEvent) -> None:
        await self._transition_claimed(
            event,
            changes={
                "status": OutboxStatus.SENT.value,
                "publishedAt": utc_now(),
                "lastError": None,
            },
        )

    async def mark_as_failed(self, event: OutboxEvent, *, error: str) -> OutboxStatus:
        """Schedule a retry, or park the event after its final failed attempt."""

        if event.attempts >= event.max_attempts:
            await self.mark_as_dead(event, error=error)
            return OutboxStatus.DEAD
        await self._transition_claimed(
            event,
            changes={
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
        await self._transition_claimed(
            event,
            changes={
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
