from __future__ import annotations

from collections import defaultdict
from contextlib import asynccontextmanager
from types import SimpleNamespace


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: defaultdict[str, list[str]] = defaultdict(list)
        self.hashes: defaultdict[str, dict[str, str]] = defaultdict(dict)

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value: str, **_kwargs):
        self.values[key] = value

    async def getdel(self, key: str):
        return self.values.pop(key, None)

    async def delete(self, *keys: str):
        for key in keys:
            self.values.pop(key, None)
            self.lists.pop(key, None)
            self.hashes.pop(key, None)

    async def lrange(self, key: str, start: int, end: int):
        return self.lists[key][start : end + 1]

    async def lpush(self, key: str, value: str):
        self.lists[key].insert(0, value)

    async def ltrim(self, key: str, start: int, end: int):
        self.lists[key] = self.lists[key][start : end + 1]

    async def expire(self, *_args):
        return True

    async def eval(
        self,
        _script: str,
        _keys: int,
        key: str,
        _now: str,
        limit: str,
        member: str,
    ):
        entries = self.lists[key]
        if len(entries) >= int(limit):
            return 0
        entries.append(member)
        return 1

    async def hset(self, key: str, *, mapping: dict[str, str]):
        self.hashes[key].update(mapping)

    async def hgetall(self, key: str):
        return self.hashes[key]

    @asynccontextmanager
    async def pipeline(self, **_kwargs):
        yield self

    async def execute(self):
        return []

    async def scan_iter(self, *, match: str):
        prefix = match[:-1]
        for key in list(self.values):
            if key.startswith(prefix):
                yield key


class FakeOutboxCollection:
    """In-memory stand-in for the outbox collection.

    Implements only what the outbox repository uses, but implements the claim
    faithfully: ``find_one_and_update`` matches and mutates one document under a
    single lock-free step, which is what makes concurrent claims safe in Mongo.
    """

    def __init__(self) -> None:
        self.documents: list[dict] = []
        self._next_id = 0

    def _allocate_id(self) -> str:
        self._next_id += 1
        return f"{self._next_id:024x}"

    async def insert_one(self, document: dict, session=None):
        key = document.get("idempotencyKey")
        if key is not None and any(d.get("idempotencyKey") == key for d in self.documents):
            from pymongo.errors import DuplicateKeyError

            raise DuplicateKeyError("duplicate idempotencyKey")
        stored = dict(document)
        stored.setdefault("_id", self._allocate_id())
        stored["_session"] = session
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    @staticmethod
    def _matches(document: dict, query: dict) -> bool:
        for field, condition in query.items():
            value = document.get(field)
            if isinstance(condition, dict):
                if "$in" in condition and value not in condition["$in"]:
                    return False
                if "$lte" in condition and not (
                    value is not None and value <= condition["$lte"]
                ):
                    return False
            elif value != condition:
                return False
        return True

    async def find_one_and_update(self, query, update, sort=None, return_document=None):
        candidates = [d for d in self.documents if self._matches(d, query)]
        if sort:
            field, direction = sort[0]
            candidates.sort(key=lambda d: d[field], reverse=direction < 0)
        if not candidates:
            return None
        document = candidates[0]
        document.update(update.get("$set", {}))
        for field, amount in update.get("$inc", {}).items():
            document[field] = document.get(field, 0) + amount
        return dict(document)

    async def update_one(self, query, update):
        for document in self.documents:
            if str(document["_id"]) == str(query["_id"]):
                document.update(update.get("$set", {}))
                return
        return

    async def find_one(self, query):
        for document in self.documents:
            if str(document["_id"]) == str(query["_id"]):
                return dict(document)
        return None
