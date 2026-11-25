"""In-memory search store: the published chunks in a dict, by document.

It stands in for OpenSearch (production and S17): one index entry per chunk,
replaced per document, with the readers as filterable fields. The filter
(:func:`ekb.contracts.admits`) is the one OpenSearch will run as a ``terms``
query on ``acl.users`` and ``acl.groups``. No ranking yet: S13 adds BM25 and
vectors. Thread-safe, lost when the process stops.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence

from ekb.contracts import Chunk, DocumentAcl, Principals, admits
from ekb.platform.registry import register
from ekb.stores.search.port import Published, UnpublishableError


@register("search", "in_memory")
class InMemorySearchStore:
    def __init__(self, current_lifetime: Callable[[str], int] | None = None) -> None:
        self._current_lifetime = current_lifetime
        self._by_document: dict[str, tuple[Chunk, ...]] = {}
        self._lock = threading.Lock()
        self._hidden: set[str] = set()

    def set_visible(self, document_key: str, *, visible: bool) -> None:
        with self._lock:
            if visible:
                self._hidden.discard(document_key)
            else:
                self._hidden.add(document_key)

    def publish(self, document_key: str, chunks: Sequence[Chunk]) -> Published:
        batch = tuple(sorted(chunks, key=lambda c: c.position))
        _check(document_key, batch)
        with self._lock:
            before = self._by_document.pop(document_key, ())
            if batch:
                self._by_document[document_key] = batch
        return Published(document_key, len(batch), len(before))

    def set_readers(self, record: DocumentAcl) -> int:
        update = {
            "acl": record.acl,
            "acl_version": record.acl_version,
            "resolver_id": record.resolver_id,
        }
        with self._lock:
            current = self._by_document.get(record.document_key, ())
            if current:
                self._by_document[record.document_key] = tuple(
                    chunk.model_copy(update=update) for chunk in current
                )
        return len(current)

    def remove(self, document_key: str) -> int:
        with self._lock:
            return len(self._by_document.pop(document_key, ()))

    def chunks(self, document_key: str | None = None) -> tuple[Chunk, ...]:
        with self._lock:
            keys = [document_key] if document_key is not None else sorted(self._by_document)
            visible: list[Chunk] = []
            for key in keys:
                if key not in self._hidden:
                    visible.extend(self._live_chunks(key))
            return tuple(visible)

    def _live_chunks(self, key: str) -> tuple[Chunk, ...]:
        batch = self._by_document.get(key, ())
        if (
            batch
            and self._current_lifetime
            and batch[0].original_lifetime != self._current_lifetime(key)
        ):
            return ()
        return batch

    def chunks_for_update(self, document_key: str) -> tuple[Chunk, ...]:
        with self._lock:
            return self._live_chunks(document_key)

    def documents(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(c.document_key for c in self.chunks()))

    def readable_by(self, principals: Principals | None) -> tuple[Chunk, ...]:
        if principals is None:
            return ()
        return tuple(c for c in self.chunks() if c.acl is not None and admits(c.acl, principals))


def _check(document_key: str, batch: tuple[Chunk, ...]) -> None:
    """The publication gate, checked again at the door: all or nothing."""
    for chunk in batch:
        if chunk.document_key != document_key:
            raise UnpublishableError(f"{chunk.chunk_id} belongs to {chunk.document_key}")
        if not chunk.publishable:
            missing = [
                name
                for name, value in (
                    ("readers (T8)", chunk.acl),
                    ("a sensitivity (T5)", chunk.sensitivity),
                    ("a vector (T7)", chunk.vector),
                )
                if value is None
            ]
            raise UnpublishableError(
                f"{chunk.chunk_id} is not publishable: missing {' and '.join(missing)}"
            )
    if len({chunk.original_lifetime for chunk in batch}) > 1:
        raise UnpublishableError(f"{document_key}: chunks belong to different lifetimes")
    versions = {chunk.document_version for chunk in batch}
    if len(versions) > 1:
        raise UnpublishableError(
            f"{document_key}: one version at a time, not {', '.join(sorted(versions))}"
        )
    ids = [chunk.chunk_id for chunk in batch]
    if len(set(ids)) != len(ids):
        raise UnpublishableError(f"{document_key}: the same chunk ID twice")
