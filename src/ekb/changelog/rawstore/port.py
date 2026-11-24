"""Port for 3 · Change log: the raw store. Every option implements :class:`RawStore`.

The raw store keeps the exact bytes each source sent, one object per item and
version, and never changes them. Why keep originals at all, when the source
still has them?

- **Later steps read the copy, not the source.** T3 parses the stored bytes
  of the version the event names. The source may have changed again, be rate
  limited or be down, and every step still sees exactly what the event saw.
- **Rebuilds and new models.** Re-parsing with a better parser or re-embedding
  with a new model (D16 #8) replays the stored originals; the sources are not
  asked again. On AWS, where the bus keeps nothing once consumed, the raw
  store plus the registry *is* the replay source (D4).

``put`` has the shape of the connectors' ``OriginalStore``
(``(document_key, version, bytes) -> pointer``), so a raw store is handed to
the connectors as is. The pointer it returns is what an upsert event carries.

**Deletes.** :meth:`purge` removes every stored version of an item. T2 calls
it when it processes a delete (S4); until then a deleted item's originals stay.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class StoredOriginal:
    """What the store knows about one stored version (not the bytes themselves)."""

    pointer: str
    document_key: str
    version: str
    size: int
    checksum: str
    """``sha256:<hex>`` of the bytes, to check a copy against."""
    stored_at: datetime


class OriginalNotFoundError(LookupError):
    """No original under this pointer: never stored, or purged after a delete."""


class ConflictingOriginalError(ValueError):
    """Different bytes for a version already stored.

    A version must name one content: T2 decides "unchanged" from it, so two
    contents under one version would make it skip a real change.
    """


@runtime_checkable
class RawStore(Protocol):
    """Immutable, versioned originals: one object per ``(document_key, version)``."""

    def put(self, document_key: str, version: str, original: bytes) -> str:
        """Store the bytes and return their pointer.

        Storing the same bytes again returns the same pointer (a repeated event
        is harmless). Different bytes for a stored version raise
        :class:`ConflictingOriginalError`.
        """
        ...

    def get(self, pointer: str) -> bytes:
        """The stored bytes, exactly as the source sent them."""
        ...

    def versions(self, document_key: str) -> list[StoredOriginal]:
        """Every version stored for an item, oldest first."""
        ...

    def purge(self, document_key: str) -> int:
        """Remove every version of an item; return how many were removed."""
        ...
