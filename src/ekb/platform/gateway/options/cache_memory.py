"""In-memory response cache: lost when the process stops. For tests and the ``ci`` profile."""

from __future__ import annotations

import threading
from collections.abc import Sequence

from ekb.platform.gateway.models import CachedReply
from ekb.platform.registry import register


@register("model_cache", "in_memory")
class InMemoryCache:
    def __init__(self) -> None:
        self._entries: dict[str, CachedReply] = {}
        self._tags: dict[str, set[str]] = {}
        self._quotes: dict[str, set[str]] = {}
        self.guard = threading.Lock()
        self._lock = threading.Lock()

    def get(self, key: str) -> CachedReply | None:
        with self._lock:
            return self._entries.get(key)

    def put(
        self,
        key: str,
        entry: CachedReply,
        document_key: str | None = None,
        quotes: Sequence[str] = (),
    ) -> None:
        with self._lock:
            self._entries[key] = entry
            if document_key is not None:
                self._tags.setdefault(key, set()).add(document_key)
            if quotes:
                self._quotes.setdefault(key, set()).update(quotes)

    def tag(self, key: str, document_key: str) -> None:
        with self._lock:
            if key in self._entries:
                self._tags.setdefault(key, set()).add(document_key)

    def quote(self, key: str, document_keys: Sequence[str]) -> None:
        with self._lock:
            if key in self._entries and document_keys:
                self._quotes.setdefault(key, set()).update(document_keys)

    def forget(self, document_key: str) -> int:
        with self._lock:
            dropped = 0
            for key in [k for k, quoted in self._quotes.items() if document_key in quoted]:
                del self._quotes[key]
                self._tags.pop(key, None)
                if self._entries.pop(key, None) is not None:
                    dropped += 1
            for key in [k for k, tags in self._tags.items() if document_key in tags]:
                self._tags[key].discard(document_key)
                if not self._tags[key]:
                    del self._tags[key]
                    self._quotes.pop(key, None)
                    self._entries.pop(key, None)
                    dropped += 1
            return dropped

    def size(self) -> int:
        with self._lock:
            return len(self._entries)
