from __future__ import annotations

from pymongo.errors import DuplicateKeyError

from app.db.mongodb import MongoDatabase
from app.domain.common import utc_now


class WebhookEventService:
    def __init__(self, db: MongoDatabase) -> None:
        self.db = db

    async def claim(self, event_id: str) -> bool:
        try:
            await self.db.webhook_events.insert_one(
                {"eventId": event_id, "createdAt": utc_now()}
            )
        except DuplicateKeyError:
            return False
        return True
