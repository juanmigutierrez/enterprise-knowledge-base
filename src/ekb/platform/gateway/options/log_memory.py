"""In-memory cost log: lost when the process stops. For tests and the ``ci`` profile."""

from __future__ import annotations

import threading

from ekb.platform.gateway.models import CallRecord
from ekb.platform.registry import register


@register("cost_log", "in_memory")
class InMemoryCostLog:
    def __init__(self) -> None:
        self._records: list[CallRecord] = []
        self.guard = threading.Lock()
        self._lock = threading.Lock()

    def append(self, record: CallRecord) -> None:
        with self._lock:
            self._records.append(record)

    def records(self) -> list[CallRecord]:
        with self._lock:
            return list(self._records)
