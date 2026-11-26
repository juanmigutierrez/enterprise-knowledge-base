"""OpenSearch search store: the published chunks in one index, readers as fields (S17).

The search index of the design (D5, D14): the same port as the in-memory
option, on OpenSearch, one node in ``docker compose``. T8 publishes from the
worker, Q2 and Q3 read from the Knowledge API, in separate processes.

**One document per chunk** in ``ekb-chunks``, under a generation-specific ID:

===========================  ========================================================
field                        why
===========================  ========================================================
``acl.users``, ``acl.groups``  the readers, filtered on (``keyword``)
``document_key`` …           which document, version and lifetime it belongs to
``indexed``                  header + section trail + text, for BM25 (``ekb_text``)
``vector_<size>``            the vector, one ``knn_vector`` field per size (cosine)
``embedding_model``          which model made it; only the same model's are compared
``chunk``                    the whole chunk as JSON, not indexed: read back unchanged
===========================  ========================================================

**The pre-filter is part of every query** (D8, card 4.13): the readers go into
the query's ``filter`` clause, so a chunk the asker may not read is never
returned and never takes one of the ``k`` places. Hidden items (a worker
recovering one, S12) and items whose change-log lifetime a delete has closed
(S11) are removed from the readable snapshot before either ranking query.

**What differs from the in-memory store, on purpose or not:**

- *BM25 statistics come from the whole index*, not from the asker's readable
  chunks: OpenSearch counts how rare a term is per shard, over every chunk. A
  restricted page full of "salary" can therefore move alice's scores, never
  her results' contents. Scores stay inside Q3 (RRF uses ranks, and answers
  carry no scores), so the side channel is ranking order at most. Written up
  as a limit (S17c walkthrough); the in-memory store keeps the stricter rule.
- *Terms are cut the same way*: a ``pattern`` tokenizer on anything that is not
  a letter or a digit, then lower case and ASCII folding: ``Días`` and ``dias``
  are one term, ``HR-PL-07`` is three. No stemming, as in memory (language
  analyzers are an option to measure, card 4.5).
- *Vectors are compared by Lucene* (cosine, ``score = (1 + cos) / 2``), and the
  store turns the score back into the cosine the port promises.

**One version at a time.** The worker stages chunks with fresh physical IDs,
then atomically switches the document's active generation in a separate index.
Readers select only active generations and repeat a paged scan if publication
changed during it. An incomplete stage leaves the previous version readable.
After the switch the single writer removes superseded and abandoned chunks.

The index and the vector fields are created on first use. Sizes: a few
thousand chunks, read with ``search_after`` pages of 500.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from ekb.contracts import Chunk, DocumentAcl, Principals
from ekb.platform.infra import InfrastructureError, endpoint, scoped
from ekb.platform.registry import register
from ekb.stores.search.port import Published, Ranking, Scored
from ekb.stores.search.rules import best, check_batch
from ekb.stores.search.text import indexed_text, terms

CHUNKS, HIDDEN = "ekb-chunks", "ekb-chunks-hidden"
PAGE = 500

TEXT_ANALYSIS = {
    "tokenizer": {"ekb_terms": {"type": "pattern", "pattern": "[^\\p{L}\\p{N}]+"}},
    "analyzer": {
        "ekb_text": {
            "type": "custom",
            "tokenizer": "ekb_terms",
            "filter": ["lowercase", "asciifolding"],
        }
    },
}
CHUNKS_INDEX: dict[str, Any] = {
    "settings": {
        "index": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "refresh_interval": "-1",
            "knn": True,
        },
        "analysis": TEXT_ANALYSIS,
    },
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "document_key": {"type": "keyword"},
            "chunk_id": {"type": "keyword"},
            "generation": {"type": "keyword"},
            "position": {"type": "integer"},
            "document_version": {"type": "keyword"},
            "original_lifetime": {"type": "integer"},
            "acl": {"properties": {"users": {"type": "keyword"}, "groups": {"type": "keyword"}}},
            "embedding_model": {"type": "keyword"},
            "indexed": {"type": "text", "analyzer": "ekb_text"},
            "chunk": {"type": "object", "enabled": False},
        },
    },
}
HIDDEN_INDEX: dict[str, Any] = {
    "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0}},
    "mappings": {"dynamic": "strict", "properties": {"document_key": {"type": "keyword"}}},
}
ACTIVE_INDEX: dict[str, Any] = {
    "settings": HIDDEN_INDEX["settings"],
    "mappings": {
        "properties": {"document_key": {"type": "keyword"}, "generation": {"type": "keyword"}}
    },
}
SORT = [{"document_key": "asc"}, {"position": "asc"}, {"chunk_id": "asc"}]


def vector_field(dimensions: int) -> str:
    """The ``knn_vector`` field holding vectors of this size."""
    return f"vector_{dimensions}"


def vector_mapping(dimensions: int) -> dict[str, Any]:
    return {
        "type": "knn_vector",
        "dimension": dimensions,
        "method": {"name": "hnsw", "space_type": "cosinesimil", "engine": "lucene"},
    }


def document(chunk: Chunk) -> dict[str, Any]:
    """What the index holds for one chunk. A vector of length 0 cannot be compared by
    cosine (Lucene refuses it), so it is kept in ``chunk`` but not indexed."""
    acl = chunk.acl
    body: dict[str, Any] = {
        "document_key": chunk.document_key,
        "chunk_id": chunk.chunk_id,
        "position": chunk.position,
        "document_version": chunk.document_version,
        "original_lifetime": chunk.original_lifetime,
        "acl": {
            "users": sorted(acl.users) if acl else [],
            "groups": sorted(acl.groups) if acl else [],
        },
        "embedding_model": chunk.embedding_model,
        "indexed": indexed_text(chunk),
        "chunk": chunk.model_dump(mode="json"),
    }
    if chunk.vector and _norm(chunk.vector):
        body[vector_field(len(chunk.vector))] = [float(v) for v in chunk.vector]
    return body


def readers_filter(principals: Principals) -> dict[str, Any]:
    """``admits`` (``ekb.contracts``) as a query: named, or in one of the groups now."""
    should: list[dict[str, Any]] = [{"term": {"acl.users": principals.user_id}}]
    if principals.groups:
        should.append({"terms": {"acl.groups": sorted(principals.groups)}})
    return {"bool": {"should": should, "minimum_should_match": 1}}


def _norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(v * v for v in vector))


def _client(url: str) -> Any:
    from opensearchpy import OpenSearch  # the search extra, only when this option is built

    return OpenSearch(hosts=[url], timeout=10, max_retries=1, retry_on_timeout=False)


@contextmanager
def _reaching(what: str) -> Iterator[None]:
    """Not reaching OpenSearch -> :class:`InfrastructureError` (retry later)."""
    from opensearchpy.exceptions import ConnectionError as Unreachable

    try:
        yield
    except Unreachable as exc:
        raise InfrastructureError(f"opensearch: {what}: {exc}") from exc


@register("search", "opensearch")
class OpenSearchSearchStore:
    def __init__(
        self,
        current_lifetime: Callable[[str], int] | None = None,
        *,
        namespace: str | None = None,
        url: str | None = None,
        client: Any = None,
    ) -> None:
        self.index = scoped(CHUNKS, namespace)
        self.hidden_index = scoped(HIDDEN, namespace)
        self.active_index = scoped("ekb-chunks-active", namespace)
        self._client = client if client is not None else _client(endpoint("opensearch", url))
        self._current_lifetime = current_lifetime
        self._ready = False
        self._vector_sizes: set[int] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------ the indexes

    def _ensure(self) -> None:
        if self._ready:
            return
        from opensearchpy.exceptions import RequestError

        with self._lock, _reaching("create the indexes"):
            for name, body in (
                (self.index, CHUNKS_INDEX),
                (self.hidden_index, HIDDEN_INDEX),
                (self.active_index, ACTIVE_INDEX),
            ):
                if self._client.indices.exists(index=name):
                    continue
                try:
                    self._client.indices.create(index=name, body=body)
                except RequestError as exc:  # another process created it first
                    if "resource_already_exists" not in str(exc):
                        raise
            mapping = self._client.indices.get_mapping(index=self.index)
            fields = mapping[self.index]["mappings"].get("properties", {})
            if "generation" not in fields:
                self._client.indices.put_mapping(
                    index=self.index, body={"properties": {"generation": {"type": "keyword"}}}
                )
            self._vector_sizes = {
                int(name.removeprefix("vector_")) for name in fields if name.startswith("vector_")
            }
            self._ready = True

    def _ensure_vectors(self, sizes: set[int]) -> None:
        for size in sorted(sizes - self._vector_sizes):
            with _reaching(f"add a field for vectors of size {size}"):
                self._client.indices.put_mapping(
                    index=self.index,
                    body={"properties": {vector_field(size): vector_mapping(size)}},
                )
            self._vector_sizes.add(size)

    # ------------------------------------------------------------ writing

    def set_visible(self, document_key: str, *, visible: bool) -> None:
        from opensearchpy.exceptions import NotFoundError

        self._ensure()
        with _reaching("hide or release an item"):
            if not visible:
                self._client.index(
                    index=self.hidden_index,
                    id=document_key,
                    body={"document_key": document_key},
                    refresh=True,
                )
                return
            with suppress(NotFoundError):  # visible already
                self._client.delete(index=self.hidden_index, id=document_key, refresh=True)

    def publish(self, document_key: str, chunks: Sequence[Chunk]) -> Published:
        batch = tuple(sorted(chunks, key=lambda c: c.position))
        check_batch(document_key, batch)
        self._ensure()
        self._ensure_vectors({len(c.vector) for c in batch if c.vector})
        query = {"term": {"document_key": document_key}}
        before = len(self._active_hits(query))
        generation = uuid4().hex
        actions: list[dict[str, Any]] = []
        for position, chunk in enumerate(batch):
            actions += [
                {"index": {"_index": self.index, "_id": f"{generation}-{position}"}},
                document(chunk) | {"generation": generation},
            ]
        with _reaching(f"publish {document_key}"):
            if actions and self._client.bulk(body=actions, refresh=True).get("errors"):
                raise InfrastructureError(f"opensearch: incomplete generation for {document_key}")
            self._client.index(
                index=self.active_index,
                id=document_key,
                refresh=True,
                body={"document_key": document_key, "generation": generation},
            )
            # A retry also collects abandoned stages from earlier failed writes.
            stale = {
                "bool": {"filter": [query], "must_not": [{"term": {"generation": generation}}]}
            }
            self._delete_matching(stale)
        return Published(document_key, len(batch), before)

    def set_readers(self, record: DocumentAcl) -> int:
        current = self._active_hits({"term": {"document_key": record.document_key}})
        if not current:
            return 0
        update = {
            "acl": record.acl,
            "acl_version": record.acl_version,
            "resolver_id": record.resolver_id,
        }
        self.publish(record.document_key, [chunk.model_copy(update=update) for _, chunk in current])
        return len(current)

    def _delete_matching(self, query: dict[str, Any]) -> None:
        result = self._client.delete_by_query(index=self.index, body={"query": query}, refresh=True)
        if result.get("failures") or result.get("timed_out") or result.get("version_conflicts"):
            raise InfrastructureError("opensearch: incomplete chunk deletion")

    def remove(self, document_key: str) -> int:
        from opensearchpy.exceptions import NotFoundError

        replaced = self.publish(document_key, ()).replaced
        with _reaching(f"remove {document_key}"), suppress(NotFoundError):
            self._client.delete(index=self.active_index, id=document_key, refresh=True)
        return replaced

    # ------------------------------------------------------------ reading

    def _search(self, index: str, body: dict[str, Any]) -> list[dict[str, Any]]:
        with _reaching(f"search {index}"):
            result = self._client.search(index=index, body=body)
        hits: list[dict[str, Any]] = result["hits"]["hits"]
        return hits

    def _pages(self, index: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        self._ensure()
        body["size"] = PAGE
        while True:
            hits = self._search(index, body)
            yield from hits
            if len(hits) < PAGE:
                return
            body["search_after"] = hits[-1]["sort"]

    def _generations(self) -> dict[str, str]:
        body = {
            "query": {"match_all": {}},
            "sort": [{"document_key": "asc"}],
            "_source": ["generation"],
        }
        return {
            hit["_id"]: hit["_source"]["generation"] for hit in self._pages(self.active_index, body)
        }

    def _active_hits(self, query: dict[str, Any]) -> list[tuple[str, Chunk]]:
        # Legacy chunks have no generation. They remain live until first replaced.
        while True:
            active = self._generations()
            legacy: list[dict[str, Any]] = [{"exists": {"field": "generation"}}]
            if active:
                legacy.append({"terms": {"document_key": list(active)}})
            versions: list[dict[str, Any]] = [{"bool": {"must_not": legacy}}]
            if active:
                versions.append({"terms": {"generation": list(active.values())}})
            selected = {
                "bool": {
                    "filter": [query, {"bool": {"should": versions, "minimum_should_match": 1}}]
                }
            }
            body = {"query": selected, "sort": SORT, "_source": ["chunk"]}
            found = [
                (str(hit["_id"]), Chunk.model_validate(hit["_source"]["chunk"]))
                for hit in self._pages(self.index, body)
            ]
            if active == self._generations():
                return found

    def _visible_hits(self, query: dict[str, Any]) -> tuple[tuple[str, Chunk], ...]:
        candidates = self._active_hits(query)
        if not candidates:
            return ()
        body = {"query": {"match_all": {}}, "sort": [{"document_key": "asc"}], "_source": False}
        hidden = {hit["_id"] for hit in self._pages(self.hidden_index, body)}
        lifetimes = {}
        if self._current_lifetime is not None:
            lifetimes = {
                key: self._current_lifetime(key) for key in {c.document_key for _, c in candidates}
            }
        return tuple(
            (physical_id, c)
            for physical_id, c in candidates
            if c.document_key not in hidden
            and lifetimes.get(c.document_key, c.original_lifetime) == c.original_lifetime
        )

    def chunks(self, document_key: str | None = None) -> tuple[Chunk, ...]:
        query = (
            {"term": {"document_key": document_key}}
            if document_key is not None
            else {"match_all": {}}
        )
        return tuple(c for _, c in self._visible_hits(query))

    def chunks_for_update(self, document_key: str) -> tuple[Chunk, ...]:
        found = [c for _, c in self._active_hits({"term": {"document_key": document_key}})]
        if (
            found
            and self._current_lifetime is not None
            and found[0].original_lifetime != self._current_lifetime(document_key)
        ):
            return ()
        return tuple(found)

    def documents(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(c.document_key for c in self.chunks()))

    def readable_by(self, principals: Principals | None) -> tuple[Chunk, ...]:
        if principals is None:
            return ()
        return tuple(c for _, c in self._visible_hits(readers_filter(principals)))

    def ranking_snapshot(self, principals: Principals | None) -> _Snapshot:
        hits = self._visible_hits(readers_filter(principals)) if principals else ()
        return _Snapshot(
            self, principals, tuple(c for _, c in hits), {c.chunk_id: i for i, c in hits}
        )

    def keyword(self, query: str, principals: Principals | None, k: int) -> Ranking:
        return self.ranking_snapshot(principals).keyword(query, k)

    def nearest(
        self, vector: Sequence[float], space: str, principals: Principals | None, k: int
    ) -> Ranking:
        return self.ranking_snapshot(principals).nearest(vector, space, k)

    # ------------------------------------------------------------ lifecycle

    def destroy(self) -> None:
        """Delete the three indexes (the end of a demo or test run)."""
        with _reaching("delete the indexes"):
            for name in (self.index, self.hidden_index, self.active_index):
                self._client.indices.delete(index=name, ignore_unavailable=True)
        self._ready = False
        self._vector_sizes = set()


@dataclass(frozen=True)
class _Snapshot:
    """The asker's readable chunks, read once; both legs rank only those, by ID."""

    store: OpenSearchSearchStore
    principals: Principals | None
    readable: tuple[Chunk, ...]
    physical_ids: dict[str, str]

    def is_current(self) -> bool:
        current = self.store.ranking_snapshot(self.principals)
        return current.physical_ids == self.physical_ids and current.readable == self.readable

    def keyword(self, query: str, k: int) -> Ranking:
        readable = self.readable
        if not readable or not terms(query) or k <= 0:
            return Ranking(hits=(), searched=len(readable))
        body = {
            "size": k,
            "query": {
                "bool": {
                    "must": [{"match": {"indexed": {"query": query}}}],
                    "filter": [
                        {"ids": {"values": [self.physical_ids[c.chunk_id] for c in readable]}}
                    ],
                }
            },
            "_source": False,
        }
        return Ranking(hits=self._scored(body, k), searched=len(readable))

    def nearest(self, vector: Sequence[float], space: str, k: int) -> Ranking:
        readable = self.readable
        comparable = [
            c
            for c in readable
            if c.embedding_model == space and c.vector is not None and len(c.vector) == len(vector)
        ]
        skipped = len(readable) - len(comparable)
        if not comparable or not _norm(vector) or k <= 0:
            return Ranking(hits=(), searched=len(readable), skipped=skipped)
        body = {
            "size": k,
            "query": {
                "knn": {
                    vector_field(len(vector)): {
                        "vector": [float(v) for v in vector],
                        "k": k,
                        "filter": {
                            "ids": {"values": [self.physical_ids[c.chunk_id] for c in comparable]}
                        },
                    }
                }
            },
            "_source": False,
        }
        # Lucene's cosine score is (1 + cos) / 2; the port promises the cosine itself.
        hits = self._scored(body, k, lambda score: 2 * score - 1)
        return Ranking(hits=hits, searched=len(readable), skipped=skipped)

    def _scored(
        self,
        body: dict[str, Any],
        k: int,
        convert: Callable[[float], float] = float,
    ) -> tuple[Scored, ...]:
        by_id = {self.physical_ids[c.chunk_id]: c for c in self.readable}
        hits = self.store._search(self.store.index, body)
        scored = [
            Scored(by_id[str(hit["_id"])], convert(float(hit["_score"])))
            for hit in hits
            if str(hit["_id"]) in by_id
        ]
        return best(scored, k)
