"""In-memory document registry: a dict from ``document_key`` to its record.

It stands in for DynamoDB (production) and DynamoDB Local (``dynamodb``, S17): one item per
document, read and written by key. Thread-safe, lost when the process stops.
"""

from __future__ import annotations

import threading

from ekb.changelog.registry.port import DocumentRecord, known_versions
from ekb.contracts.listing import ListingVersion
from ekb.platform.registry import register


@register("registry", "in_memory")
class InMemoryRegistry:
    def __init__(self) -> None:
        self._records: dict[str, DocumentRecord] = {}
        self._lock = threading.Lock()

    def get(self, document_key: str) -> DocumentRecord | None:
        with self._lock:
            return self._records.get(document_key)

    def put(self, record: DocumentRecord) -> None:
        with self._lock:
            self._records[record.document_key] = record

    def known(self, source: str) -> dict[str, ListingVersion]:
        return known_versions(self.records(source))

    def records(self, source: str | None = None) -> list[DocumentRecord]:
        with self._lock:
            records = list(self._records.values())
        return sorted(
            (r for r in records if source is None or r.source == source),
            key=lambda r: r.document_key,
        )
