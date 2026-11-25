"""Port for 5 · Entity store. Every option implements :class:`EntityStore`.

The store holds the canonical entities: who and what the company knows, each
under one ID (``person:bob``, ``team:T-PPL``). In S9 they come from the HR
record API: every employee and team record that T2 sends on becomes one
entity, and purging the record removes it (``ekb.process.t6_entities.sync``).

**One record defines at most one entity.** ``put`` replaces whatever the same
record defined before, so a record whose person mapping changed leaves no
stale entity behind, and ``remove`` takes a record's key, which is all a
delete event carries.

**What it does not hold: which documents mention an entity.** Those links
travel on the chunks (``Chunk.entity_ids``) into the search index (D6: "entity
IDs are written onto each chunk"). There they are filtered by the reader's
permissions with the chunk itself, and they disappear when the chunk does. A
second copy here would need the same permission checks and the same purges.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ekb.contracts import Entity, EntityKind


@runtime_checkable
class EntityStore(Protocol):
    """The canonical entities, by ID, each linked to the record that defines it."""

    def put(self, entity: Entity) -> Entity | None:
        """Add or replace an entity; return the one its record defined before, if any."""
        ...

    def remove(self, source_key: str) -> Entity | None:
        """Remove the entity defined by this record (a purge); return it, if there was one."""
        ...

    def get(self, entity_id: str) -> Entity | None: ...

    def entities(self, kind: EntityKind | None = None) -> tuple[Entity, ...]:
        """Every entity (or every one of ``kind``), sorted by ID."""
        ...
