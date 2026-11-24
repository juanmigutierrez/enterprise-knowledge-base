"""In-memory raw store: originals in a dict, under S3-like pointers.

A pointer looks like ``mem://ekb-raw/hr_docs/HR-PL-04/2.1``: bucket, source,
source ID, version. Each part is URL-encoded, because IDs and versions can
contain ``/`` or ``:`` (``employees/E-1001``, an ISO timestamp). The store never
parses a pointer back; it is only a key.

It stands in for S3 with versioning (production) and SeaweedFS (S17).
Thread-safe, lost when the process stops.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

from ekb.changelog.rawstore.port import (
    ConflictingOriginalError,
    OriginalNotFoundError,
    StoredOriginal,
)
from ekb.platform.registry import register


def _now() -> datetime:
    return datetime.now(UTC)


def _checksum(original: bytes) -> str:
    return f"sha256:{hashlib.sha256(original).hexdigest()}"


@dataclass(frozen=True)
class _Object:
    meta: StoredOriginal
    data: bytes
    lifetime: int


@register("rawstore", "in_memory")
class InMemoryRawStore:
    def __init__(self, bucket: str = "ekb-raw", clock: Callable[[], datetime] = _now) -> None:
        self._bucket = bucket
        self._clock = clock
        self._objects: dict[str, _Object] = {}
        self._by_key: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def pointer_for(self, document_key: str, version: str, *, lifetime: int = 0) -> str:
        source, _, source_id = document_key.partition(":")
        parts = (quote(part, safe="") for part in (source, source_id, version))
        base = f"mem://{self._bucket}/" + "/".join(parts)
        return f"{base}?lifetime={lifetime}" if lifetime else base

    def put(self, document_key: str, version: str, original: bytes, *, lifetime: int = 0) -> str:
        pointer = self.pointer_for(document_key, version, lifetime=lifetime)
        checksum = _checksum(original)
        with self._lock:
            existing = self._objects.get(pointer)
            if existing is not None:
                if existing.meta.checksum != checksum:
                    raise ConflictingOriginalError(
                        f"{document_key} version {version} is already stored with other bytes"
                    )
                return pointer
            meta = StoredOriginal(
                pointer=pointer,
                document_key=document_key,
                version=version,
                size=len(original),
                checksum=checksum,
                stored_at=self._clock(),
            )
            self._objects[pointer] = _Object(meta, bytes(original), lifetime)
            self._by_key.setdefault(document_key, []).append(pointer)
        return pointer

    def get(self, pointer: str) -> bytes:
        with self._lock:
            stored = self._objects.get(pointer)
        if stored is None:
            raise OriginalNotFoundError(f"no original at {pointer} (never stored, or purged)")
        return stored.data

    def versions(self, document_key: str) -> list[StoredOriginal]:
        with self._lock:
            return [self._objects[p].meta for p in self._by_key.get(document_key, [])]

    def purge(self, document_key: str, *, lifetime: int | None = None) -> int:
        with self._lock:
            pointers = self._by_key.pop(document_key, [])
            kept = []
            for pointer in pointers:
                if lifetime is None or self._objects[pointer].lifetime == lifetime:
                    del self._objects[pointer]
                else:
                    kept.append(pointer)
            if kept:
                self._by_key[document_key] = kept
        return len(pointers) - len(kept)
