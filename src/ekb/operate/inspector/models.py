"""What the inspector API returns. This is the contract the UI's TypeScript types
are generated from (``ui/openapi.json`` → ``ui/src/api/schema.d.ts``), so a
change here is a change the UI must follow; CI fails if the generated types
are stale.

They are view models, separate from ``ChangeEvent`` on purpose: the internal
contract can evolve without breaking the UI, and lists are plain sorted lists
(JSON has no sets).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from ekb.changelog.bus.port import BusStats
from ekb.changelog.rawstore.port import StoredOriginal
from ekb.changelog.registry.port import DocumentRecord
from ekb.contracts import Acl, ChangeEvent, ChangeOp
from ekb.operate.inspector.log import LoggedEvent


class _View(BaseModel):
    model_config = ConfigDict(frozen=True)


class AclView(_View):
    users: list[str]
    groups: list[str]

    @classmethod
    def of(cls, acl: Acl) -> AclView:
        return cls(users=sorted(acl.users), groups=sorted(acl.groups))


class EventView(_View):
    """One event as received, with the log's arrival number and its place in the bus."""

    seq: int
    received_at: datetime
    partition: int
    offset: int
    document_key: str
    source: str
    source_id: str
    op: ChangeOp
    version: str
    occurred_at: datetime
    acl: AclView
    person_id: str | None
    changed_by: str | None
    raw_pointer: str | None
    content_hash: str | None
    schema_version: int

    @classmethod
    def of(cls, entry: LoggedEvent) -> EventView:
        event: ChangeEvent = entry.event
        return cls(
            seq=entry.seq,
            received_at=entry.received_at,
            partition=entry.partition,
            offset=entry.offset,
            document_key=event.document_key,
            source=event.source,
            source_id=event.source_id,
            op=event.op,
            version=event.version,
            occurred_at=event.occurred_at,
            acl=AclView.of(event.acl),
            person_id=event.person_id,
            changed_by=event.changed_by,
            raw_pointer=event.raw_pointer,
            content_hash=event.content_hash,
            schema_version=event.schema_version,
        )


class EventPage(_View):
    """Events after a cursor, and the cursor to pass next time (for live polling)."""

    events: list[EventView]
    cursor: int


class DocumentView(_View):
    """An item's current state: its latest event, and how many it has had."""

    document_key: str
    source: str
    source_id: str
    deleted: bool
    last: EventView
    events: int


class SourceView(_View):
    source: str
    items: int  # not deleted
    deleted: int
    events: int
    last_event_at: datetime | None


class GroupView(_View):
    """A consumer group: per partition, the offset of its next pending event."""

    group: str
    committed: list[int]
    lag: int


class ChangeLogView(_View):
    """The event bus: how many events each partition holds, and how far each group has read."""

    partitions: int
    end_offsets: list[int]
    events: int
    groups: list[GroupView]

    @classmethod
    def of(cls, stats: BusStats) -> ChangeLogView:
        return cls(
            partitions=len(stats.end_offsets),
            end_offsets=list(stats.end_offsets),
            events=stats.events,
            groups=[
                GroupView(group=g.group, committed=list(g.committed), lag=g.lag)
                for g in stats.groups
            ],
        )


class RecordView(_View):
    """One registry record: what the knowledge base last processed for an item."""

    document_key: str
    source: str
    source_id: str
    version: str
    deleted: bool
    content_hash: str | None
    acl: AclView
    raw_pointer: str | None
    person_id: str | None
    changed_by: str | None
    updated_at: datetime
    events: int
    originals: int
    """How many versions of the item the raw store holds."""

    @classmethod
    def of(cls, record: DocumentRecord, originals: int) -> RecordView:
        return cls(
            document_key=record.document_key,
            source=record.source,
            source_id=record.source_id,
            version=record.version,
            deleted=record.deleted,
            content_hash=record.content_hash,
            acl=AclView.of(record.acl),
            raw_pointer=record.raw_pointer,
            person_id=record.person_id,
            changed_by=record.changed_by,
            updated_at=record.updated_at,
            events=record.events,
            originals=originals,
        )


class OriginalView(_View):
    """One stored version of an item's original: where it is and how big, not its bytes."""

    pointer: str
    version: str
    size: int
    checksum: str
    stored_at: datetime

    @classmethod
    def of(cls, original: StoredOriginal) -> OriginalView:
        return cls(
            pointer=original.pointer,
            version=original.version,
            size=original.size,
            checksum=original.checksum,
            stored_at=original.stored_at,
        )
