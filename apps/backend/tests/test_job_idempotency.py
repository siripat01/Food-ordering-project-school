from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymongo.errors import DuplicateKeyError

from app.services.idempotency import IdempotencyService


@pytest.mark.asyncio
async def test_is_completed_reads_the_completion_marker() -> None:
    collection = AsyncMock()
    collection.find_one.return_value = {"_id": "marker-1"}
    service = IdempotencyService(SimpleNamespace(job_idempotency=collection))

    assert await service.is_completed(scope="agent.process", key="line:event-1") is True

    collection.find_one.assert_awaited_once_with(
        {"scope": "agent.process", "key": "line:event-1"},
        projection={"_id": 1},
    )


@pytest.mark.asyncio
async def test_missing_completion_marker_remains_retryable() -> None:
    collection = AsyncMock()
    collection.find_one.return_value = None
    service = IdempotencyService(SimpleNamespace(job_idempotency=collection))

    assert await service.is_completed(scope="agent.process", key="line:event-1") is False


@pytest.mark.asyncio
async def test_mark_completed_persists_only_successful_completion() -> None:
    collection = AsyncMock()
    service = IdempotencyService(SimpleNamespace(job_idempotency=collection))

    await service.mark_completed(scope="agent.process", key="line:event-1")

    document = collection.insert_one.await_args.args[0]
    assert document["scope"] == "agent.process"
    assert document["key"] == "line:event-1"
    assert document["completedAt"].tzinfo is not None
    assert document["expiresAt"] > document["completedAt"]


@pytest.mark.asyncio
async def test_concurrent_completion_marker_is_harmless() -> None:
    collection = AsyncMock()
    collection.insert_one.side_effect = DuplicateKeyError("already completed")
    service = IdempotencyService(SimpleNamespace(job_idempotency=collection))

    await service.mark_completed(scope="agent.process", key="line:event-1")
