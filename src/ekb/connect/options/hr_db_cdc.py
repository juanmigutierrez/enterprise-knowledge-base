"""CDC connector for the HR database: change data capture, as Debezium does it.

It reads the database's change log after the last ``seq`` it saw. Starting at
0 it gets the snapshot (every row once, ``op = r``) and then every insert,
update **and delete** in commit order, so, unlike the HR API, nothing is ever
missed and no full listing is needed.

People: a leave row says ``E-1001``; the :class:`PersonMap` turns that into
the identity user for ``person_id`` and the ACL. ``changed_by`` is the row's
``changed_by`` column (the application's writer), also checked.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from itertools import groupby

import httpx

from ekb.connect.hr_db import CdcEntry, CdcReader
from ekb.connect.normalize import hr_db as normalize
from ekb.connect.normalize.common import canonical_json, content_hash
from ekb.connect.people import PersonMap
from ekb.connect.port import EventSink, OriginalStore
from ekb.contracts import ChangeEvent, ChangeOp
from ekb.platform.registry import register


@register("connect", "hr_db_cdc")
class HrDbCdcConnector:
    def __init__(
        self,
        reader: CdcReader,
        sink: EventSink,
        store_original: OriginalStore,
        people: PersonMap,
        source: str = "hr_db",
        start_after: int = 0,
    ) -> None:
        self._reader = reader
        self._sink = sink
        self._store_original = store_original
        self._people = people
        self._source = source
        self._cursor = start_after
        # A resumed cursor alone cannot prove which ACL was last published.
        self._restore = start_after > 0
        self._leave: dict[str, tuple[CdcEntry, ChangeEvent | None]] = {}

    @property
    def source(self) -> str:
        return self._source

    @property
    def cursor(self) -> int:
        """``seq`` of the last entry turned into an event."""
        return self._cursor

    def sync(self) -> int:
        emitted = 0
        try:
            self._people.refresh()
            directory_available = True
        except (httpx.HTTPError, OSError) as exc:
            # CDC still has to propagate deletes while the HR directory is offline.
            logging.getLogger(__name__).warning("Using HR-only leave permissions: %s", exc)
            directory_available = False
        if self._restore:
            for entry in self._reader.read(0):
                if entry.seq > self._cursor:
                    break
                self._remember(entry, None)
            self._restore = False
        for seq, batch in groupby(self._reader.read(self._cursor), key=lambda entry: entry.seq):
            for entry in batch:
                event = self._normalize(entry, directory_available)
                self._sink(event)
                self._remember(entry, event)
                emitted += 1
            self._cursor = seq

        for source_id, (entry, previous) in self._leave.items():
            employee = normalize.person_key(entry)
            owner = (
                self._people.for_employee(employee, refresh_on_miss=False)
                if directory_available and employee
                else None
            )
            if previous is not None and owner == previous.person_id:
                continue
            now = datetime.now(UTC)
            event = ChangeEvent(
                source=self._source,
                source_id=source_id,
                op=ChangeOp.ACL_CHANGE,
                version=f"{entry.seq}/acl/{now.isoformat()}",
                occurred_at=now,
                person_id=owner,
                acl=normalize.rule_for(entry.table).acl(owner),
                content_hash=content_hash(canonical_json(entry.after or {})),
            )
            self._sink(event)
            self._leave[source_id] = entry, event
            emitted += 1
        return emitted

    def _remember(self, entry: CdcEntry, event: ChangeEvent | None) -> None:
        if entry.table != "leave_balances":
            return
        source_id = normalize.source_id_of(entry)
        if entry.op == normalize.DELETE_OP:
            self._leave.pop(source_id, None)
        else:
            self._leave[source_id] = entry, event

    def _normalize(self, entry: CdcEntry, directory_available: bool) -> ChangeEvent:
        if entry.op == normalize.DELETE_OP:
            return normalize.event_for(
                self._source,
                entry,
                store_original=self._store_original,
                person_id=None,
                changed_by=None,
            )
        key = normalize.person_key(entry)
        person = (
            self._people.for_employee(key, refresh_on_miss=False)
            if directory_available and key
            else None
        )
        writer = (entry.after or {}).get("changed_by")
        return normalize.event_for(
            self._source,
            entry,
            store_original=self._store_original,
            person_id=person,
            changed_by=self._people.user(writer)
            if directory_available and isinstance(writer, str)
            else None,
        )
