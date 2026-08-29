"""Bounded in-process TTL cache for fully-serialised responses.

The catalogue is read-mostly and every Android cold start hits the same handful
of URLs, so caching the finished bytes skips both the query and JSON encoding.
One cache per worker; no locking is needed because get/set never await.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Generic, TypeVar

V = TypeVar("V")


class TTLCache(Generic[V]):
    __slots__ = ("_data", "_maxsize", "_ttl", "hits", "misses")

    def __init__(self, maxsize: int, ttl: float) -> None:
        self._data: OrderedDict[str, tuple[float, V]] = OrderedDict()
        self._maxsize = max(1, maxsize)
        self._ttl = ttl
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> V | None:
        entry = self._data.get(key)
        if entry is None:
            self.misses += 1
            return None
        expires_at, value = entry
        if expires_at <= time.monotonic():
            del self._data[key]
            self.misses += 1
            return None
        self._data.move_to_end(key)
        self.hits += 1
        return value

    def set(self, key: str, value: V) -> None:
        self._data[key] = (time.monotonic() + self._ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()

    def stats(self) -> dict[str, int]:
        return {"entries": len(self._data), "hits": self.hits, "misses": self.misses}
