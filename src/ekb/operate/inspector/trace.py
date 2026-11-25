"""What the inspector keeps of the worker pipeline (S12): every attempt's trace.

``ekb inspector`` hands each trace the worker writes (``on_trace``) to a
:class:`TraceLog`, failed attempts and parked events included, so the
**Pipeline** tab can show one document's way through T2 … T8, event by event.

A trace holds no content (``ekb.contracts.trace``), so a deleted document's
traces stay: they are the history of what happened to it, the purge included.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from ekb.contracts import Trace, TraceStatus


@dataclass(frozen=True)
class LoggedTrace:
    seq: int  # arrival order in this log, 1, 2, 3 ...
    trace: Trace


class TraceLog:
    """Every trace, oldest first. ``append`` is the worker's ``on_trace`` in the inspector."""

    def __init__(self) -> None:
        self._entries: list[LoggedTrace] = []
        self._lock = threading.Lock()

    def append(self, trace: Trace) -> None:
        with self._lock:
            self._entries.append(LoggedTrace(len(self._entries) + 1, trace))

    @property
    def cursor(self) -> int:
        with self._lock:
            return len(self._entries)

    def entries(
        self,
        *,
        since: int = 0,
        document_key: str | None = None,
        source: str | None = None,
    ) -> list[LoggedTrace]:
        with self._lock:
            entries = self._entries[since:]
        return [
            e
            for e in entries
            if (document_key is None or e.trace.document_key == document_key)
            and (source is None or e.trace.source == source)
        ]

    def latest(
        self, *, source: str | None = None, status: TraceStatus | None = None
    ) -> dict[str, tuple[LoggedTrace, int]]:
        """Per item: its latest trace and how many it has, filtered on the latest one."""
        latest: dict[str, tuple[LoggedTrace, int]] = {}
        for entry in self.entries(source=source):
            key = entry.trace.document_key
            count = latest[key][1] + 1 if key in latest else 1
            latest[key] = (entry, count)
        if status is None:
            return latest
        return {k: v for k, v in latest.items() if v[0].trace.status is status}
