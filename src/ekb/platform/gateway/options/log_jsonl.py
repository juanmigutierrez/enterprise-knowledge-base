"""JSON Lines cost log: one record per line, appended, kept across runs.

``.cache/ekb/model-calls.jsonl`` by default (git-ignored). Every process that
calls models appends to the same file, and the inspector reads it, so quotas
and the spend are counted across the demo, the inspector and later steps alike.

A line holds a :class:`~ekb.platform.gateway.models.CallRecord`: no prompt and no
reply. A line that cannot be read (a process killed mid-write) is skipped and
counted in :attr:`JsonlCostLog.skipped`, never fatal: losing one record of a log
is better than refusing every model call.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from filelock import FileLock
from pydantic import ValidationError

from ekb.platform.gateway.models import CallRecord
from ekb.platform.registry import register

logger = logging.getLogger(__name__)


@register("cost_log", "jsonl")
class JsonlCostLog:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.skipped = 0
        self.guard = FileLock(str(Path(path).resolve()) + ".lock")
        self._lock = threading.Lock()
        self._version: tuple[int, int] | None = None
        self._cached: list[CallRecord] = []

    def append(self, record: CallRecord) -> None:
        line = (record.model_dump_json() + "\n").encode("utf-8")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a+b") as file:
                file.seek(0, os.SEEK_END)
                if file.tell() > 0:
                    file.seek(-1, os.SEEK_END)
                    if file.read(1) != b"\n":
                        file.write(b"\n")  # a write was cut short: start a fresh line
                file.write(line)

    def records(self) -> list[CallRecord]:
        """Refresh records after appends or edits to reconciled charges."""
        with self._lock:
            if not self.path.exists():
                return []
            stat = self.path.stat()
            version = (stat.st_size, stat.st_mtime_ns)
            if version != self._version:
                self._cached, self.skipped = _parse(self.path.read_bytes())
                self._version = version
            return list(self._cached)


def _parse(text: bytes) -> tuple[list[CallRecord], int]:
    records: list[CallRecord] = []
    skipped = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = CallRecord.model_validate_json(line)
            if record.error and ": " in record.error:
                # Older SDK diagnostics appended an untrusted provider message after the type.
                record = record.model_copy(update={"error": record.error.partition(": ")[0]})
            records.append(record)
        except ValidationError:
            skipped += 1
    if skipped:
        logger.warning("cost log: skipped %d unreadable line(s)", skipped)
    return records, skipped
