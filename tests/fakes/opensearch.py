"""An OpenSearch client in memory: the part of the query language the search store uses.

It answers ``bool`` (``filter``, ``must``, ``must_not``, ``should`` with
``minimum_should_match``), ``term``, ``terms``, ``ids``, ``match_all``, ``match``
(BM25 over the whole index, as OpenSearch counts it), ``knn`` with a ``filter``
(cosine, scored ``(1 + cos) / 2`` like Lucene), ``sort`` with ``search_after``,
a ``terms`` aggregation with a ``min`` inside, bulk writes, delete by query, and
a strict mapping (a field the mapping lacks is refused, as OpenSearch does).

It is a stand-in for running the store's logic in every pull request. Whether
OpenSearch reads the same queries the same way is what the integration tests
check (``-m integration``, after ``make up``).
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from opensearchpy.exceptions import ConnectionError as Unreachable
from opensearchpy.exceptions import NotFoundError, RequestError

from ekb.stores.search.text import terms

K1, B = 1.2, 0.75


def _value(doc: dict[str, Any], path: str) -> Any:
    current: Any = doc
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _values(doc: dict[str, Any], path: str) -> list[Any]:
    found = _value(doc, path)
    if found is None:
        return []
    return list(found) if isinstance(found, list) else [found]


class _Indices:
    def __init__(self, cluster: FakeOpenSearch) -> None:
        self.cluster = cluster

    def exists(self, index: str) -> bool:
        self.cluster.reach()
        return index in self.cluster.indexes

    def create(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        self.cluster.reach()
        if index in self.cluster.indexes:
            raise RequestError(400, "resource_already_exists_exception", {})
        self.cluster.indexes[index] = {}
        self.cluster._version += 1
        self.cluster.mappings[index] = {
            "properties": dict(body["mappings"]["properties"]),
            "settings": body["settings"],
        }
        return {"acknowledged": True}

    def get_mapping(self, index: str) -> dict[str, Any]:
        self.cluster.reach()
        properties = self.cluster.mappings[index]["properties"]
        return {index: {"mappings": {"properties": dict(properties)}}}

    def put_mapping(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        self.cluster.reach()
        self.cluster.mappings[index]["properties"].update(body["properties"])
        return {"acknowledged": True}

    def delete(self, index: str, ignore_unavailable: bool = False) -> dict[str, Any]:
        self.cluster.indexes.pop(index, None)
        self.cluster._version += 1
        self.cluster.mappings.pop(index, None)
        return {"acknowledged": True}


class FakeOpenSearch:
    def __init__(self) -> None:
        self.indexes: dict[str, dict[str, dict[str, Any]]] = {}
        self.mappings: dict[str, dict[str, Any]] = {}
        self.indices = _Indices(self)
        self.down = False
        """Set to make every call fail as if OpenSearch could not be reached."""
        self.fail_bulk_after: int | None = None
        """Set to make a bulk request fail from its n-th action on (0: all of it)."""
        self.searches = 0
        self._version = 0
        self._counts: dict[tuple[str, str], tuple[int, dict[str, Counter[str]]]] = {}

    def reach(self) -> None:
        if self.down:
            raise Unreachable("N/A", "opensearch is down", Exception("refused"))

    # ------------------------------------------------------------ writes

    def _check_fields(self, index: str, body: dict[str, Any]) -> None:
        allowed = self.mappings[index]["properties"]
        for name in body:
            if name not in allowed:
                raise RequestError(400, "strict_dynamic_mapping_exception", {"field": name})

    def index(
        self, index: str, id: str, body: dict[str, Any], refresh: bool = False
    ) -> dict[str, Any]:
        self.reach()
        self._check_fields(index, body)
        self._version += 1
        self.indexes[index][id] = body
        return {"result": "created"}

    def delete(self, index: str, id: str, refresh: bool = False) -> dict[str, Any]:
        self.reach()
        if id not in self.indexes[index]:
            raise NotFoundError(404, "not_found", {})
        self._version += 1
        del self.indexes[index][id]
        return {"result": "deleted"}

    def bulk(self, body: list[dict[str, Any]], refresh: bool = False) -> dict[str, Any]:
        self.reach()
        self._version += 1
        items: list[dict[str, Any]] = []
        position = 0
        actions = iter(body)
        for action in actions:
            ((kind, meta),) = action.items()
            failed = self.fail_bulk_after is not None and position >= self.fail_bulk_after
            position += 1
            if kind == "index":
                source = next(actions)
                if failed:
                    items.append({kind: {"_id": meta["_id"], "error": {"type": "boom"}}})
                    continue
                try:
                    self._check_fields(meta["_index"], source)
                except RequestError as exc:
                    items.append({kind: {"_id": meta["_id"], "error": {"type": str(exc)}}})
                    continue
                self.indexes[meta["_index"]][meta["_id"]] = source
                items.append({kind: {"_id": meta["_id"], "result": "created"}})
            elif kind == "delete":
                if failed:
                    items.append({kind: {"_id": meta["_id"], "error": {"type": "boom"}}})
                    continue
                self.indexes[meta["_index"]].pop(meta["_id"], None)
                items.append({kind: {"_id": meta["_id"], "result": "deleted"}})
        errors = any("error" in next(iter(item.values())) for item in items)
        return {"errors": errors, "items": items}

    def delete_by_query(
        self, index: str, body: dict[str, Any], refresh: bool = False, conflicts: str = "abort"
    ) -> dict[str, Any]:
        self.reach()
        self._version += 1
        docs = self.indexes[index]
        gone = [i for i, doc in docs.items() if self._matches(i, doc, body["query"], index)]
        for i in gone:
            del docs[i]
        return {"deleted": len(gone)}

    # ------------------------------------------------------------ search

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        self.reach()
        self.searches += 1
        docs = self.indexes[index]
        query = body.get("query", {"match_all": {}})
        if "aggs" in body:
            return {"hits": {"hits": []}, "aggregations": self._aggregate(docs, body["aggs"])}
        if "knn" in query:
            scored = self._knn(index, query["knn"])
        else:
            scored = [
                (i, self._score(i, doc, query, index))
                for i, doc in docs.items()
                if self._matches(i, doc, query, index)
            ]
        if "sort" in body:
            keys = [next(iter(rule)) for rule in body["sort"]]
            ordered = sorted(scored, key=lambda item: [_value(docs[item[0]], k) for k in keys])
            after = body.get("search_after")
            if after is not None:
                ordered = [
                    item for item in ordered if [_value(docs[item[0]], k) for k in keys] > after
                ]
        else:
            ordered = sorted(scored, key=lambda item: -item[1])
        hits = []
        for i, score in ordered[: body.get("size", 10)]:
            hit: dict[str, Any] = {"_id": i, "_score": score}
            source = body.get("_source", True)
            if source is True:
                hit["_source"] = docs[i]
            elif isinstance(source, list):
                hit["_source"] = {name: docs[i][name] for name in source if name in docs[i]}
            if "sort" in body:
                hit["sort"] = [_value(docs[i], next(iter(rule))) for rule in body["sort"]]
            hits.append(hit)
        return {"hits": {"hits": hits}}

    def _aggregate(self, docs: dict[str, dict[str, Any]], aggs: dict[str, Any]) -> dict[str, Any]:
        ((name, spec),) = aggs.items()
        field = spec["terms"]["field"]
        ((inner, inner_spec),) = spec["aggs"].items()
        groups: dict[Any, list[Any]] = {}
        for doc in docs.values():
            groups.setdefault(_value(doc, field), []).append(
                _value(doc, inner_spec["min"]["field"])
            )
        buckets = [
            {"key": key, "doc_count": len(values), inner: {"value": float(min(values))}}
            for key, values in sorted(groups.items())
        ]
        return {name: {"buckets": buckets}}

    def _matches(self, i: str, doc: dict[str, Any], query: dict[str, Any], index: str) -> bool:
        ((kind, spec),) = query.items()
        if kind == "match_all":
            return True
        if kind == "exists":
            return _value(doc, spec["field"]) is not None
        if kind == "term":
            ((field, value),) = spec.items()
            return value in _values(doc, field)
        if kind == "terms":
            ((field, values),) = spec.items()
            return bool(set(_values(doc, field)) & set(values))
        if kind == "ids":
            return i in set(spec["values"])
        if kind == "match":
            ((field, wanted),) = spec.items()
            text = wanted["query"] if isinstance(wanted, dict) else wanted
            return bool(set(terms(text)) & set(terms(str(_value(doc, field) or ""))))
        if kind == "bool":
            if not all(self._matches(i, doc, q, index) for q in spec.get("filter", [])):
                return False
            if not all(self._matches(i, doc, q, index) for q in spec.get("must", [])):
                return False
            if any(self._matches(i, doc, q, index) for q in spec.get("must_not", [])):
                return False
            should = spec.get("should", [])
            if should:
                wanted_count = int(spec.get("minimum_should_match", 1))
                return sum(self._matches(i, doc, q, index) for q in should) >= wanted_count
            return True
        raise ValueError(f"the fake does not answer {kind!r} queries")

    def _counted(self, index: str, field: str) -> dict[str, Counter[str]]:
        """Each document's terms in ``field``, counted once per change of the index."""
        key = (index, field)
        cached = self._counts.get(key)
        if cached is not None and cached[0] == self._version:
            return cached[1]
        docs = self.indexes[index]
        counted = {i: Counter(terms(str(_value(d, field) or ""))) for i, d in docs.items()}
        self._counts[key] = (self._version, counted)
        return counted

    def _score(self, i: str, doc: dict[str, Any], query: dict[str, Any], index: str) -> float:
        """BM25 for the ``match`` clauses, with statistics over the whole index."""
        ((kind, spec),) = query.items()
        if kind == "bool":
            return sum(self._score(i, doc, q, index) for q in spec.get("must", []))
        if kind != "match":
            return 0.0
        ((field, wanted),) = spec.items()
        text = wanted["query"] if isinstance(wanted, dict) else wanted
        counted = self._counted(index, field)
        total = len(counted)
        average = sum(sum(c.values()) for c in counted.values()) / total if total else 1.0
        mine = counted[i]
        length = sum(mine.values())
        score = 0.0
        for term in set(terms(text)):
            if not mine[term]:
                continue
            holding = sum(1 for c in counted.values() if c[term])
            idf = math.log(1 + (total - holding + 0.5) / (holding + 0.5))
            tf = mine[term]
            score += idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / (average or 1.0)))
        return score

    def _knn(self, index: str, spec: dict[str, Any]) -> list[tuple[str, float]]:
        ((field, wanted),) = spec.items()
        vector = wanted["vector"]
        query_norm = math.sqrt(sum(v * v for v in vector))
        scored = []
        for i, doc in self.indexes[index].items():
            theirs = doc.get(field)
            if theirs is None or (
                "filter" in wanted and not self._matches(i, doc, wanted["filter"], index)
            ):
                continue
            norm = math.sqrt(sum(v * v for v in theirs))
            cosine = sum(a * b for a, b in zip(vector, theirs, strict=True)) / (query_norm * norm)
            scored.append((i, (1 + cosine) / 2))
        return sorted(scored, key=lambda item: -item[1])[: wanted["k"]]
