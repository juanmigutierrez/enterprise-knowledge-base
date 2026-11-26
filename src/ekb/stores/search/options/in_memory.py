"""In-memory search store: the published chunks in a dict, by document.

It stands in for OpenSearch (production; ``opensearch``, S17): one index entry per chunk,
replaced per document, with the readers as filterable fields. The filter
(:func:`ekb.contracts.admits`) is the one OpenSearch will run as a ``terms``
query on ``acl.users`` and ``acl.groups``. Thread-safe, lost when the process
stops.

**Ranking (S13).** Both legs first keep the chunks the principals admit, then
rank only those:

- ``keyword``: Okapi BM25, Lucene's variant (``k1`` 1.2, ``b`` 0.75). A term
  scores more the rarer it is among the chunks searched (``idf``) and the more
  often it appears in a chunk, with diminishing returns (``k1``), less in a
  longer-than-average chunk (``b``). The statistics are computed over the
  admitted chunks only, so a restricted page cannot shift anyone else's scores.
  OpenSearch computes them per shard over every document (the
  ``opensearch`` option, S17c).
- ``nearest``: cosine similarity between the question's vector and each
  chunk's, for chunks embedded with the same model (``space``).

Every query scans the admitted chunks: fine for a few thousand, which is all
the POC holds. Each chunk's terms are counted once, when it is published.
"""

from __future__ import annotations

import math
import threading
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ekb.contracts import Chunk, DocumentAcl, Principals, admits
from ekb.platform.registry import register
from ekb.stores.search.port import Published, Ranking, RankingSnapshot, Scored
from ekb.stores.search.rules import best, check_batch
from ekb.stores.search.text import indexed_text, terms

K1 = 1.2
"""How quickly repeating a term stops adding to the score (Lucene's default)."""
B = 0.75
"""How much a long chunk is penalized for holding more words (Lucene's default)."""


@dataclass(frozen=True)
class _Terms:
    """One chunk's term counts and length, counted when it was published."""

    counts: Counter[str]
    length: int

    @classmethod
    def of(cls, chunk: Chunk) -> _Terms:
        found = terms(indexed_text(chunk))
        return cls(Counter(found), len(found))


@register("search", "in_memory")
class InMemorySearchStore:
    def __init__(self, current_lifetime: Callable[[str], int] | None = None) -> None:
        self._current_lifetime = current_lifetime
        self._by_document: dict[str, tuple[Chunk, ...]] = {}
        self._terms: dict[str, _Terms] = {}  # chunk ID -> its counted terms
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
        check_batch(document_key, batch)
        counted = {chunk.chunk_id: _Terms.of(chunk) for chunk in batch}
        with self._lock:
            before = self._by_document.pop(document_key, ())
            for chunk in before:
                self._terms.pop(chunk.chunk_id, None)
            if batch:
                self._by_document[document_key] = batch
                self._terms.update(counted)
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
            removed = self._by_document.pop(document_key, ())
            for chunk in removed:
                self._terms.pop(chunk.chunk_id, None)
            return len(removed)

    def chunks(self, document_key: str | None = None) -> tuple[Chunk, ...]:
        with self._lock:
            return self._visible(document_key)

    def _visible(self, document_key: str | None = None) -> tuple[Chunk, ...]:
        """Published, not hidden, not of a closed lifetime. The caller holds the lock."""
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
        with self._lock:
            return self._readable(principals)

    def _readable(self, principals: Principals | None) -> tuple[Chunk, ...]:
        """The pre-filter: visible chunks these principals admit. The caller holds the lock."""
        if principals is None:
            return ()
        return tuple(c for c in self._visible() if c.acl is not None and admits(c.acl, principals))

    def ranking_snapshot(self, principals: Principals | None) -> RankingSnapshot:
        with self._lock:
            readable = self._readable(principals)
            counted = tuple(self._terms[chunk.chunk_id] for chunk in readable)
        return _Snapshot(readable, counted, lambda: self.readable_by(principals) == readable)

    def keyword(self, query: str, principals: Principals | None, k: int) -> Ranking:
        return self.ranking_snapshot(principals).keyword(query, k)

    def nearest(
        self, vector: Sequence[float], space: str, principals: Principals | None, k: int
    ) -> Ranking:
        return self.ranking_snapshot(principals).nearest(vector, space, k)


@dataclass(frozen=True)
class _Snapshot:
    readable: tuple[Chunk, ...]
    terms_by_chunk: tuple[_Terms, ...]
    is_current: Callable[[], bool]

    def keyword(self, query: str, k: int) -> Ranking:
        wanted = set(terms(query))
        readable = self.readable
        counted = tuple(zip(readable, self.terms_by_chunk, strict=True))
        if not counted or not wanted or k <= 0:
            return Ranking(hits=(), searched=len(readable))
        total = len(counted)
        average = sum(t.length for _, t in counted) / total or 1.0
        holding = Counter(term for _, t in counted for term in wanted if t.counts[term])
        idf = {term: math.log(1 + (total - n + 0.5) / (n + 0.5)) for term, n in holding.items()}
        scored = []
        for chunk, counts in counted:
            score = 0.0
            for term, weight in idf.items():
                tf = counts.counts[term]
                if tf:
                    norm = K1 * (1 - B + B * counts.length / average)
                    score += weight * tf * (K1 + 1) / (tf + norm)
            if score > 0:
                scored.append(Scored(chunk, score))
        return Ranking(hits=best(scored, k), searched=total)

    def nearest(self, vector: Sequence[float], space: str, k: int) -> Ranking:
        readable = self.readable
        comparable = [
            (chunk, chunk.vector)
            for chunk in readable
            if chunk.embedding_model == space
            and chunk.vector is not None
            and len(chunk.vector) == len(vector)
        ]
        skipped = len(readable) - len(comparable)
        query_norm = _norm(vector)
        if not comparable or not query_norm or k <= 0:
            return Ranking(hits=(), searched=len(readable), skipped=skipped)
        scored = []
        for chunk, theirs in comparable:
            norm = _norm(theirs)
            if norm:
                dot = sum(a * b for a, b in zip(vector, theirs, strict=True))
                scored.append(Scored(chunk, dot / (query_norm * norm)))
        return Ranking(hits=best(scored, k), searched=len(readable), skipped=skipped)


def _norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(v * v for v in vector))
