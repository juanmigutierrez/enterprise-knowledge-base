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

from ekb.contracts import ChangeEvent, ChangeOp
from ekb.operate.inspector.log import LoggedEvent


class _View(BaseModel):
    model_config = ConfigDict(frozen=True)


class AclView(_View):
    users: list[str]
    groups: list[str]


class EventView(_View):
    """One event as received, with the log's arrival number."""

    seq: int
    received_at: datetime
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
            document_key=event.document_key,
            source=event.source,
            source_id=event.source_id,
            op=event.op,
            version=event.version,
            occurred_at=event.occurred_at,
            acl=AclView(users=sorted(event.acl.users), groups=sorted(event.acl.groups)),
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
