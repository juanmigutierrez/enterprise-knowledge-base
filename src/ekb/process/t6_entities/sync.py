"""Keeping the entity store current from record events (S9).

The records path of the write path, beside the documents path::

    HR record event ─ T2 process ─▶ T3: "not a document" ─▶ handle(event): read, put
                    ─ T2 purge   ─────────────────────────▶ forget(key): remove

``handle`` reads the record's stored original (the exact version the event
names, as T3 does for a page) and puts the entity it defines; ``forget``
removes the entity a purged record defined. So a new hire becomes someone to
link to, a renamed team is matched by its new name, and a leaver, once the
full listing finds the delete (S1b's delta never reports one), is no one to
link to.

**Links already written are not redone here.** A document processed before a
hire keeps no link to them, and one processed before a leaver keeps theirs,
until the document changes or is rebuilt (the S9 plan; re-linking joins the
self-maintenance scenario, S16). The store never returns a removed entity, so
nothing about the leaver can be looked up from that ID.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ekb.changelog.rawstore.port import RawStore
from ekb.contracts import ChangeEvent, ChangeOp, Entity
from ekb.process.t6_entities.records import RecordReader
from ekb.stores.entities.port import EntityStore


@dataclass(frozen=True)
class RecordResult:
    """What one record event did to the entity store."""

    event: ChangeEvent
    entity: Entity | None
    """The entity the record defines now; ``None`` if it defines none."""
    replaced: Entity | None
    """What the same record defined before, if anything."""
    reason: str


class EntitySync:
    """The entity store, fed by the record sources that define entities."""

    def __init__(
        self, store: EntityStore, rawstore: RawStore, readers: Mapping[str, RecordReader]
    ) -> None:
        self._store = store
        self._rawstore = rawstore
        self._readers = dict(readers)

    def handles(self, source: str) -> bool:
        """Whether ``source``'s records define entities."""
        return source in self._readers

    def handle(self, event: ChangeEvent) -> RecordResult:
        """Put the entity this upsert's record defines (or drop the one it no longer does)."""
        reader = self._readers.get(event.source)
        if reader is None:
            raise ValueError(f"{event.source} defines no entities")
        if event.op is not ChangeOp.UPSERT or event.raw_pointer is None:
            raise ValueError(f"only an upsert defines an entity, not {event.op.value}")
        entity = reader(event, self._rawstore.get(event.raw_pointer))
        if entity is None:
            replaced = self._store.remove(event.document_key)
            why = "defines no entity (no identity user for this employee: fail closed)"
            if replaced is not None:
                why += f"; {replaced.entity_id} removed"
            return RecordResult(event, None, replaced, why)
        replaced = self._store.put(entity)
        verb = "added" if replaced is None else "updated"
        return RecordResult(event, entity, replaced, f"{entity.entity_id} {verb}: {entity.name}")

    def forget(self, document_key: str) -> Entity | None:
        """A purged record's entity leaves the store; returns it, if there was one."""
        return self._store.remove(document_key)
