"""What the inspector shows of T8 (S11): each document's readers and publication status,
and who is in which group now.

The panel reads the same stores the live loop's T8 writes: the search store (the
published chunks, with their readers) and the permission store (memberships and
each document's readers record). So the **T8 Permissions** tab shows exactly what
a search would filter on right now.

**Deleted content is never shown.** A document is shown only while the original
it was published from is still the live one: a delete closes that lifetime as
soon as it is published, a moment before T2's purge takes the chunks out of the
search store, and the panel already hides it (as the T3, T4 and T6 panels do).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ekb.contracts import Chunk, DocumentAcl, Membership
from ekb.operate.inspector.log import EntityLifetimes
from ekb.stores.permissions.port import PermissionStore
from ekb.stores.search.port import SearchStore


class PublishedLifetimes(EntityLifetimes):
    """Which lifetime of its original each document's readers and chunks came from."""


@dataclass(frozen=True)
class PermissionSnapshot:
    """The live part of both stores, read once."""

    chunks: tuple[Chunk, ...]
    records: tuple[DocumentAcl, ...]
    memberships: tuple[Membership, ...]


@dataclass(frozen=True)
class PermissionPanel:
    index: SearchStore
    permissions: PermissionStore
    resolver_id: str = ""
    lifetimes: PublishedLifetimes = field(default_factory=PublishedLifetimes)

    def snapshot(self, current_lifetime: Callable[[str], int]) -> PermissionSnapshot:
        """Published chunks and readers records of live documents, and every membership."""
        # The writer publishes before recording the lifetime. Read the lifetimes
        # first, so a re-created document cannot validate an older version's chunks.
        lifetimes = self.lifetimes.snapshot()

        def live(document_key: str) -> bool:
            return lifetimes.get(document_key) == current_lifetime(document_key)

        return PermissionSnapshot(
            chunks=tuple(c for c in self.index.chunks() if live(c.document_key)),
            records=tuple(r for r in self.permissions.all_readers() if live(r.document_key)),
            memberships=tuple(
                m
                for m in self.permissions.memberships()
                if m.original_lifetime == current_lifetime(m.source_key)
            ),
        )
