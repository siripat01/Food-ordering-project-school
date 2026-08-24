from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any

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
    """Short-lived, per-process confirmation state keyed by authenticated user ID."""

    def __init__(self, *, ttl_minutes: int) -> None:
        self.ttl = timedelta(minutes=ttl_minutes)
        self.entries: dict[str, PendingAction] = {}

    def _purge_expired(self) -> None:
        now = datetime.now(UTC)
        self.entries = {
            user_id: action
            for user_id, action in self.entries.items()
            if action.expires_at > now
        }

    def put(
        self,
        *,
        user_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        idempotency_key: str,
    ) -> PendingAction:
        self._purge_expired()
        action = PendingAction(
            tool_name=tool_name,
            arguments=arguments,
            idempotency_key=idempotency_key,
            expires_at=datetime.now(UTC) + self.ttl,
        )
        self.entries[user_id] = action
        return action

    def get(self, user_id: str) -> PendingAction | None:
        self._purge_expired()
        return self.entries.get(user_id)

    def consume(self, user_id: str) -> PendingAction | None:
        self._purge_expired()
        return self.entries.pop(user_id, None)

    def clear(self, user_id: str) -> None:
        self.entries.pop(user_id, None)


class PerUserRateLimiter:
    """Bounded in-process sliding-window limiter for authenticated LLM requests."""

    def __init__(self, *, requests_per_minute: int) -> None:
        self.limit = requests_per_minute
        self.entries: dict[str, deque[float]] = {}

    def allow(self, user_id: str) -> bool:
        now = monotonic()
        cutoff = now - 60
        active: dict[str, deque[float]] = {}
        for key, timestamps in self.entries.items():
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if timestamps:
                active[key] = timestamps
        self.entries = active
        timestamps = self.entries.setdefault(user_id, deque())
        if len(timestamps) >= self.limit:
            return False
        timestamps.append(now)
        return True
