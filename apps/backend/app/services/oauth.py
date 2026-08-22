from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any

from app.core.config import Settings
from app.db.mongodb import MongoDatabase
from app.domain.common import utc_now


class OAuthStateService:
    def __init__(self, db: MongoDatabase, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    @staticmethod
    def _hash(state: str) -> str:
        return hashlib.sha256(state.encode("utf-8")).hexdigest()

    async def store(
        self,
        *,
        state: str,
        nonce: str,
        origin: str,
        chat_user_id: str | None,
    ) -> None:
        now = utc_now()
        await self.db.oauth_states.insert_one(
            {
                "stateHash": self._hash(state),
                "nonce": nonce,
                "origin": origin,
                "chatUserId": chat_user_id,
                "createdAt": now,
                "expiresAt": now + timedelta(minutes=self.settings.oauth_state_ttl_minutes),
            }
        )

    async def consume(self, state: str) -> dict[str, Any] | None:
        return await self.db.oauth_states.find_one_and_delete(
            {"stateHash": self._hash(state), "expiresAt": {"$gt": utc_now()}},
            sort=[("createdAt", 1)],
        )
