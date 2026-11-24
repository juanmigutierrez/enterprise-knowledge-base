"""The inspector's own copies: every event, and every T2 decision, in arrival order.

Since S3 the inspector is a consumer group (``inspector``) on the event bus,
like any worker: ``ekb inspector`` drains the group into this log after every
sync round. It keeps the events it has read so the UI can page through them
with a cursor and show each item's history, and it records where each event
sits in the bus (partition and offset).

It no longer says what each source holds: the full listings ask the document
registry for that (S3).

Since S4 it also keeps T2's decisions (:class:`DecisionLog`): ``ekb inspector``
hands it every result T2 produces, so the UI can show what was processed,
fast-tracked, skipped or purged, and why.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from ekb.changelog.bus.port import Delivery
from ekb.contracts import ChangeEvent, DiffAction, DiffDecision


@dataclass(frozen=True)
class LoggedEvent:
    seq: int  # arrival order in this log, 1, 2, 3 ...
    received_at: datetime
    partition: int
    offset: int
    event: ChangeEvent


class EventLog:
    def __init__(self) -> None:
        self._entries: list[LoggedEvent] = []
        self._lock = threading.Lock()

    def append(self, delivery: Delivery) -> None:
        """A bus handler: ``drain(bus, "inspector", log.append)``."""
        with self._lock:
            self._entries.append(
                LoggedEvent(
                    seq=len(self._entries) + 1,
                    received_at=datetime.now(UTC),
                    partition=delivery.partition,
                    offset=delivery.offset,
                    event=delivery.event,
                )
            )

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


@dataclass(frozen=True)
class LoggedDecision:
    seq: int  # order in which T2 decided, 1, 2, 3 ...
    decided_at: datetime
    decision: DiffDecision
    purged: int  # originals removed from the raw store (deletes only)


class DecisionLog:
    """T2's decisions, oldest first. ``append`` is T2's ``downstream`` in the inspector."""

    def __init__(self) -> None:
        self._entries: list[LoggedDecision] = []
        self._lock = threading.Lock()

    def append(self, decision: DiffDecision, purged: int = 0) -> None:
        with self._lock:
            self._entries.append(
                LoggedDecision(
                    seq=len(self._entries) + 1,
                    decided_at=datetime.now(UTC),
                    decision=decision,
                    purged=purged,
                )
            )

    @property
    def cursor(self) -> int:
        with self._lock:
            return len(self._entries)

    def entries(
        self,
        *,
        since: int = 0,
        source: str | None = None,
        action: DiffAction | None = None,
        document_key: str | None = None,
    ) -> list[LoggedDecision]:
        with self._lock:
            entries = self._entries[since:]
        return [
            e
            for e in entries
            if (source is None or e.decision.event.source == source)
            and (action is None or e.decision.action is action)
            and (document_key is None or e.decision.event.document_key == document_key)
        ]

    def counts(self, upto: int | None = None) -> dict[DiffAction, int]:
        """How many decisions of each kind, over the first ``upto`` (default: all)."""
        counts = dict.fromkeys(DiffAction, 0)
        for entry in self.entries()[:upto]:
            counts[entry.decision.action] += 1
        return counts
