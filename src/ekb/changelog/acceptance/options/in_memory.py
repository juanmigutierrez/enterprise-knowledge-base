"""In-memory acceptance log: a dict from ``document_key`` to its entry.

What the change log kept in its own fields until S17. Enough while the
connectors, the worker and the API share one process (the demos, the inspector,
``ekb api``); the ``dynamodb`` option shares it between processes.
Thread-safe, lost when the process stops.
"""

from __future__ import annotations

import threading

from ekb.changelog.acceptance.port import Accepted, accepted_after
from ekb.changelog.bus.port import Delivery
from ekb.changelog.registry.port import DocumentRecord
from ekb.platform.registry import register


@register("acceptance", "in_memory")
class InMemoryAcceptanceLog:
    def __init__(self) -> None:
        self._entries: dict[str, Accepted] = {}
        self._lock = threading.Lock()

    def get(self, document_key: str) -> Accepted | None:
        with self._lock:
            return self._entries.get(document_key)

    def accept(self, delivery: Delivery) -> Accepted:
        key = delivery.event.document_key
        with self._lock:
            entry = accepted_after(self._entries.get(key), delivery)
            self._entries[key] = entry
            return entry

    def records(self, source: str | None = None) -> list[DocumentRecord]:
        with self._lock:
            records = [entry.record for entry in self._entries.values()]
        return sorted(
            (r for r in records if source is None or r.source == source),
            key=lambda r: r.document_key,
        )
