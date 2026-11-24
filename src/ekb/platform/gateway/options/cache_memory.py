"""In-memory response cache: lost when the process stops. For tests and the ``ci`` profile."""

from __future__ import annotations

import threading

from ekb.platform.gateway.models import CachedReply
from ekb.platform.registry import register


@register("model_cache", "in_memory")
class InMemoryCache:
    def __init__(self) -> None:
        self._entries: dict[str, CachedReply] = {}
        self._tags: dict[str, set[str]] = {}
        self.guard = threading.Lock()
        self._lock = threading.Lock()

    def get(self, key: str) -> CachedReply | None:
        with self._lock:
            return self._entries.get(key)

    def put(self, key: str, entry: CachedReply, document_key: str | None = None) -> None:
        with self._lock:
            self._entries[key] = entry
            if document_key is not None:
                self._tags.setdefault(key, set()).add(document_key)

    def tag(self, key: str, document_key: str) -> None:
        with self._lock:
            if key in self._entries:
                self._tags.setdefault(key, set()).add(document_key)

    def forget(self, document_key: str) -> int:
        with self._lock:
            dropped = 0
            for key in [k for k, tags in self._tags.items() if document_key in tags]:
                self._tags[key].discard(document_key)
                if not self._tags[key]:
                    del self._tags[key]
                    self._entries.pop(key, None)
                    dropped += 1
            return dropped

    def size(self) -> int:
        with self._lock:
            return len(self._entries)
