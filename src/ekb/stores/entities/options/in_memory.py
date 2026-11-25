"""In-memory entity store: a dict from entity ID to entity.

It stands in for DynamoDB (production) and DynamoDB Local (S17): one item per
entity, read by ID, plus an index from the defining record to its entity (a
DynamoDB secondary index later). Thread-safe, lost when the process stops.
"""

from __future__ import annotations

import threading

from ekb.contracts import Entity, EntityKind
from ekb.platform.registry import register


@register("entities", "in_memory")
class InMemoryEntityStore:
    def __init__(self) -> None:
        self._by_id: dict[str, Entity] = {}
        self._by_source: dict[str, str] = {}  # record key -> entity ID
        self._lock = threading.Lock()

    def put(self, entity: Entity) -> Entity | None:
        with self._lock:
            before = self._pop_source(entity.source_key)
            holder = self._by_id.get(entity.entity_id)
            if holder is not None and holder.source_key != entity.source_key:
                # Another record defined this ID before: the newer record owns it now.
                self._by_source.pop(holder.source_key, None)
            self._by_id[entity.entity_id] = entity
            self._by_source[entity.source_key] = entity.entity_id
            return before

    def remove(self, source_key: str) -> Entity | None:
        with self._lock:
            return self._pop_source(source_key)

    def get(self, entity_id: str) -> Entity | None:
        with self._lock:
            return self._by_id.get(entity_id)

    def entities(self, kind: EntityKind | None = None) -> tuple[Entity, ...]:
        with self._lock:
            found = [e for e in self._by_id.values() if kind is None or e.kind is kind]
        return tuple(sorted(found, key=lambda e: e.entity_id))

    def _pop_source(self, source_key: str) -> Entity | None:
        entity_id = self._by_source.pop(source_key, None)
        return self._by_id.pop(entity_id, None) if entity_id is not None else None
