from __future__ import annotations

from collections import defaultdict
from contextlib import asynccontextmanager


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
