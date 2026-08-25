from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.domain.common import utc_now


class OutboxEventType(StrEnum):
    """Facts: business state changes that already happened and were committed.

    Only events that the application actually produces belong here. The
    dispatcher translates them into commands (:class:`app.domain.jobs.TaskName`).
    """

    ORDER_CREATED = "order.created"
    ORDER_STATUS_CHANGED = "order.status_changed"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"
    DEAD = "dead"


DEFAULT_MAX_ATTEMPTS = 5

#: Staged backoff applied after a failed dispatch attempt. The value at index
#: ``attempts - 1`` is used; the last value repeats for any further attempt.
RETRY_BACKOFF_SECONDS: tuple[int, ...] = (5, 30, 120, 300, 900)

#: How long a claimed event may stay in ``processing`` before another dispatcher
#: is allowed to reclaim it. This is what makes a crashed dispatcher recoverable.
CLAIM_VISIBILITY_TIMEOUT_SECONDS = 60


def retry_delay_seconds(attempts: int) -> int:
    """Return the backoff for an event that has already been attempted ``attempts`` times."""
    if attempts < 1:
        return RETRY_BACKOFF_SECONDS[0]
    index = min(attempts, len(RETRY_BACKOFF_SECONDS)) - 1
    return RETRY_BACKOFF_SECONDS[index]


def next_available_at(attempts: int, *, now: datetime | None = None) -> datetime:
    return (now or utc_now()) + timedelta(seconds=retry_delay_seconds(attempts))


class OutboxEvent(BaseModel):
    """In-memory view of one committed domain fact awaiting dispatch."""

    id: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: OutboxStatus = OutboxStatus.PENDING
    created_at: datetime
    published_at: datetime | None = None
    attempts: int = 0
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    available_at: datetime
    last_error: str | None = None
    correlation_id: str | None = None
    idempotency_key: str | None = None

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> OutboxEvent:
        return cls(
            id=str(document["_id"]),
            event_type=str(document.get("eventType", "")),
            payload=dict(document.get("payload") or {}),
            status=OutboxStatus(str(document.get("status", OutboxStatus.PENDING))),
            created_at=document["createdAt"],
            published_at=document.get("publishedAt"),
            attempts=int(document.get("attempts", 0)),
            max_attempts=int(document.get("maxAttempts", DEFAULT_MAX_ATTEMPTS)),
            available_at=document.get("availableAt") or document["createdAt"],
            last_error=document.get("lastError"),
            correlation_id=document.get("correlationId"),
            idempotency_key=document.get("idempotencyKey"),
        )
