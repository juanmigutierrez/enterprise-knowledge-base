"""Port for 3 · Change log: the document registry (:class:`DocumentRegistry`).

The registry is the knowledge base's memory of every tracked item: for each
``document_key``, the last change it **processed** (version, content hash,
readers, pointer to the original). Two parts of the system ask it questions:

- **T2 (S4)** compares each new event with the record, to skip what has not
  changed, fast-track a permissions-only change, and purge a delete.
- **The full listings** (S2, S2a) compare a source with :meth:`known`: an item
  the registry knows but the source no longer lists has been deleted (D3).

Since S4, T2 writes the registry: it records each event once it has decided
what the event needs and handed that on (``ekb inspector``, ``ekb demo t2``).
Only ``ekb demo s3`` still uses S3's stand-in, which records without deciding.

**A delete leaves a tombstone, not a gap.** The record stays, marked
``deleted``, with its last version and time, but without anything about the
content: no hash, readers, pointer or person, the same rule as a delete event.
The history is kept ("this existed and was deleted at ..."); the content is not.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, model_validator

from ekb.contracts import Acl, ChangeEvent, ChangeOp
from ekb.contracts.listing import ListingVersion


class DocumentRecord(BaseModel):
    """What the knowledge base last processed for one item."""

    model_config = ConfigDict(frozen=True)

    document_key: str
    source: str
    source_id: str
    version: str
    deleted: bool = False
    content_hash: str | None = None
    acl: Acl = Acl()
    raw_pointer: str | None = None
    person_id: str | None = None
    changed_by: str | None = None
    updated_at: datetime
    """``occurred_at`` of the last event recorded."""
    events: int = 1
    """How many events have been recorded for this item."""

    @model_validator(mode="after")
    def _tombstone_holds_no_content(self) -> Self:
        if self.deleted and (
            self.content_hash is not None
            or self.raw_pointer is not None
            or self.acl != Acl()
            or self.person_id is not None
        ):
            raise ValueError("a deleted record must not keep a hash, a pointer, an ACL or a person")
        return self


def record_after(previous: DocumentRecord | None, event: ChangeEvent) -> DocumentRecord:
    """The record once ``event`` is processed. Pure: the same inputs, the same record.

    ======================  =====================================================
    ``upsert``              everything from the event; a deleted item comes back
    ``acl_change``          new readers and version; keep only a matching original
    ``delete``              tombstone: version, time and who deleted; no content
    ======================  =====================================================

    An ``acl_change`` for a deleted item leaves it deleted. New readers alone
    are no evidence the content is back, so it fails closed. If the source
    really has the item again, the next upsert (or full listing) brings it back.
    """
    events = previous.events + 1 if previous else 1
    common = {
        "document_key": event.document_key,
        "source": event.source,
        "source_id": event.source_id,
        "version": event.version,
        "updated_at": event.occurred_at,
        "changed_by": event.changed_by,
        "events": events,
    }
    deleted_before = previous is not None and previous.deleted
    if event.op is ChangeOp.DELETE or (event.op is ChangeOp.ACL_CHANGE and deleted_before):
        return DocumentRecord(**common, deleted=True)
    if event.op is ChangeOp.ACL_CHANGE:
        # Apply the ACL now. A pointer is usable only while the event still
        # describes that original. A live record without one needs a full fetch.
        pointer = previous.raw_pointer if previous else None
        if (
            previous
            and event.content_hash is not None
            and event.content_hash != previous.content_hash
        ):
            pointer = None
        return DocumentRecord(
            **common,
            content_hash=event.content_hash or (previous.content_hash if previous else None),
            raw_pointer=pointer,
            acl=event.acl,
            person_id=event.person_id,
        )
    return DocumentRecord(
        **common,
        content_hash=event.content_hash,
        raw_pointer=event.raw_pointer,
        acl=event.acl,
        person_id=event.person_id,
    )


@runtime_checkable
class DocumentRegistry(Protocol):
    """Per ``document_key``, the last processed state, deletes included (as tombstones)."""

    def get(self, document_key: str) -> DocumentRecord | None:
        """The item's record, or ``None`` if it was never recorded."""
        ...

    def put(self, record: DocumentRecord) -> None:
        """Store ``record``, replacing the item's previous one."""
        ...

    def known(self, source: str) -> dict[str, ListingVersion]:
        """``source_id -> version`` of the source's items that are not deleted.

        A NeedsOriginal value forces a fetch even at the same version and
        retains the last observed version for a delete if the item is absent.
        """
        ...

    def records(self, source: str | None = None) -> list[DocumentRecord]:
        """Every record, tombstones included, sorted by ``document_key``."""
        ...


def record_event(registry: DocumentRegistry, event: ChangeEvent) -> DocumentRecord:
    """Record ``event`` as processed and return the new record."""
    record = record_after(registry.get(event.document_key), event)
    registry.put(record)
    return record
