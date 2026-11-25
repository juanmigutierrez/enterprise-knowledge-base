"""Port for 5 · Search store. Every option implements :class:`SearchStore`.

The search store holds the **published** chunks: the only ones an agent can
ever find (D8, D9). T8 writes to it (S11), T2's purge removes from it, and the
access layer reads it (Q2 + Q3, S13).

Three rules are part of the port, so no option can break them:

- **The publication gate.** :meth:`~SearchStore.publish` refuses, as a whole,
  any batch holding a chunk that is not ``publishable`` (no readers, no
  sensitivity or no vector, ``Chunk.publishable``). T8 checks first; the store
  checks again, because a chunk in the index is a chunk someone can find.
- **One version at a time.** Publishing a document replaces *all* its chunks: a
  new version is a new set (``Chunk.chunk_id`` names a place, not a text), so a
  shorter version leaves no stale passage behind.
- **Deleted is gone.** :meth:`~SearchStore.remove` drops every chunk of a
  document, and nothing removed is ever returned.

**Readers are filtered in the store**, the way OpenSearch filters on its
``acl.users`` and ``acl.groups`` fields: :meth:`~SearchStore.readable_by`
takes one person's principals (their user ID and groups *now*, from the
permission store) and returns only the chunks those admit. The search store
never looks up a group itself: the stores are independent, and who is in a
group changes without any chunk changing. S11 filters only; S13 adds ranking
(BM25 + vectors) to this port.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ekb.contracts import Chunk, DocumentAcl, Principals


class UnpublishableError(ValueError):
    """A batch the search store must not hold: nothing of it was published."""


@dataclass(frozen=True)
class Published:
    """What one :meth:`SearchStore.publish` did."""

    document_key: str
    published: int
    """Chunks of the document in the store now."""
    replaced: int
    """Chunks of its previous version that were dropped."""


@runtime_checkable
class SearchStore(Protocol):
    """The published chunks, by document, each with its readers."""

    def set_visible(self, document_key: str, *, visible: bool) -> None:
        """Hide an item during recovery, or release its completed state to readers."""
        ...

    def publish(self, document_key: str, chunks: Sequence[Chunk]) -> Published:
        """Replace the document's chunks with ``chunks``, all or nothing.

        Raises :class:`UnpublishableError` (and changes nothing) if a chunk is not
        publishable, belongs to another document, or the chunks are not one
        version with distinct IDs. No chunks at all removes the document.
        """
        ...

    def set_readers(self, record: DocumentAcl) -> int:
        """T2's fast lane: put ``record``'s readers on every chunk of its document.

        Only ``acl``, ``acl_version`` and ``resolver_id`` change: the text and the
        vector stay. Returns how many chunks were updated (0 if none is published).
        """
        ...

    def remove(self, document_key: str) -> int:
        """Drop every chunk of a deleted document; returns how many there were."""
        ...

    def chunks(self, document_key: str | None = None) -> tuple[Chunk, ...]:
        """The published chunks (of one document, or all), by document then position."""
        ...

    def chunks_for_update(self, document_key: str) -> tuple[Chunk, ...]:
        """Current-lifetime chunks, including a batch hidden during worker recovery."""
        ...

    def documents(self) -> tuple[str, ...]:
        """The keys of the documents with published chunks, sorted."""
        ...

    def readable_by(self, principals: Principals | None) -> tuple[Chunk, ...]:
        """The published chunks these principals may read; none for ``None``.

        ``None`` is an unknown or inactive person: they read nothing.
        """
        ...
