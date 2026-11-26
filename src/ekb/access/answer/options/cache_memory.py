"""``answer_cache: in_memory``: answers kept in this process, the oldest dropped first.

Lost when the process stops, which suits the POC: the in-memory stores it
answers from are lost too. A durable cache (DynamoDB or Redis) comes with the
durable stores (S17); whatever keeps the answers, reuse is decided by
``ekb.app.assistant`` after a fresh Q4 check, never by the cache.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

from ekb.access.answer.port import CachedAnswer
from ekb.platform.registry import register

VERSION = 1
MAX_ENTRIES = 1000


@register("answer_cache", "in_memory")
class InMemoryAnswerCache:
    def __init__(self, *, max_entries: int = MAX_ENTRIES) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._max = max_entries
        self._entries: OrderedDict[str, CachedAnswer] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def cache_id(self) -> str:
        return f"in_memory@{VERSION}(max={self._max})"

    def get(self, key: str) -> CachedAnswer | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
            return entry

    def put(self, key: str, entry: CachedAnswer) -> None:
        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def drop(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def forget(self, document_key: str) -> int:
        with self._lock:
            gone = [k for k, e in self._entries.items() if document_key in e.documents()]
            for key in gone:
                del self._entries[key]
            return len(gone)

    def size(self) -> int:
        with self._lock:
            return len(self._entries)
