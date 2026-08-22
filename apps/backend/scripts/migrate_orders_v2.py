from __future__ import annotations

import argparse
import asyncio
from typing import Any

from pymongo import UpdateOne

from app.core.config import get_settings
from app.db.mongodb import MongoDatabase
from app.domain.common import serialize_mongo
from app.services.orders import OrderService


async def migrate(*, apply_changes: bool, batch_size: int) -> None:
    db = MongoDatabase(get_settings())
    await db.connect()
    scanned = 0
    migrated = 0
    operations: list[UpdateOne[dict[str, Any]]] = []
    try:
        cursor = db.orders.find({"schemaVersion": {"$ne": 2}})
        async for document in cursor:
            scanned += 1
            normalized = OrderService.to_response(document)
            update = serialize_mongo(
                {
                    "items": normalized.items,
                    "subtotal": normalized.subtotal,
                    "total": normalized.total,
                    "status": normalized.status.value,
                    "statusHistory": [
                        {
                            "status": entry.status.value,
                            "changedAt": entry.changed_at,
                            "actorId": entry.actor_id,
                            "actorRole": entry.actor_role.value if entry.actor_role else None,
                        }
                        for entry in normalized.status_history
                    ],
                    "createdAt": normalized.created_at,
                    "updatedAt": normalized.updated_at,
                    "legacyPriceUnverified": True,
                    "schemaVersion": 2,
                }
            )
            operations.append(UpdateOne({"_id": document["_id"]}, {"$set": update}))
            if len(operations) >= batch_size:
                if apply_changes:
                    result = await db.orders.bulk_write(operations, ordered=False)
                    migrated += result.modified_count
                operations.clear()
        if operations and apply_changes:
            result = await db.orders.bulk_write(operations, ordered=False)
            migrated += result.modified_count
    finally:
        await db.close()
    mode = "apply" if apply_changes else "dry-run"
    print(f"mode={mode} scanned={scanned} migrated={migrated}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Idempotently backfill legacy order documents to schema v2."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes. Without this flag the command is read-only.",
    )
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()
    asyncio.run(migrate(apply_changes=args.apply, batch_size=args.batch_size))


if __name__ == "__main__":
    main()
