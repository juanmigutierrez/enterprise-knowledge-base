"""An in-memory event log: every ``ChangeEvent`` received, in arrival order.

Until the change log exists (S3) this is where the inspector's events come
from: ``ekb inspector`` wires every connector's sink to :meth:`EventLog.append`.
It can also say what each source is believed to hold (:meth:`known`), which is
what the full-listing connectors compare against, the job the document
registry takes over in S3.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from ekb.contracts import ChangeEvent, ChangeOp


@dataclass(frozen=True)
class LoggedEvent:
    seq: int  # arrival order in this log, 1, 2, 3 ...
    received_at: datetime
    event: ChangeEvent


class EventLog:
    def __init__(self) -> None:
        self._entries: list[LoggedEvent] = []
        self._lock = threading.Lock()

    def append(self, event: ChangeEvent) -> None:
        """An ``EventSink``: connectors call it with each event."""
        with self._lock:
            self._entries.append(LoggedEvent(len(self._entries) + 1, datetime.now(UTC), event))

    @property
    def cursor(self) -> int:
        with self._lock:
            return len(self._entries)

    def entries(
        self,
        *,
        since: int = 0,
        source: str | None = None,
        document_key: str | None = None,
    ) -> list[LoggedEvent]:
        with self._lock:
            entries = self._entries[since:]
        return [
            e
            for e in entries
            if (source is None or e.event.source == source)
            and (document_key is None or e.event.document_key == document_key)
        ]

    def latest(self) -> dict[str, tuple[LoggedEvent, int]]:
        """Per item: its latest event and how many events it has had."""
        latest: dict[str, tuple[LoggedEvent, int]] = {}
        for entry in self.entries():
            key = entry.event.document_key
            count = latest[key][1] + 1 if key in latest else 1
            latest[key] = (entry, count)
        return latest

    def known(self, source: str) -> dict[str, str]:
        """``source_id -> version`` of the items of ``source`` that are not deleted."""
        return {
            entry.event.source_id: entry.event.version
            for entry, _ in self.latest().values()
            if entry.event.source == source and entry.event.op is not ChangeOp.DELETE
        }
