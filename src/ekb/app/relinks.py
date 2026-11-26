"""The worker's re-link queue (S16): pages to process again because the world they
describe changed, not their text.

Two things put a page here:

1. **An entity change** (a hire, a leaver, a renamed team). The worker notes
   the change when the entity store changes (:meth:`Relinks.changed`); after
   the drain, :meth:`ekb.app.pipeline.Pipeline.relink` turns the changes into
   the pages they affect (:mod:`ekb.process.t6_entities.relink`) and keeps
   those whose links are stale.
2. **A failed context header** (S8: a header model that did not answer leaves
   the page's headers empty, and the page goes on). Once per listing round the
   worker queues such pages, so they get their headers when a model answers.

**Why not inside the entity's own event.** One HR record can affect many
pages; if any of them failed, the record's event would come back, be retried
and eventually be parked, and the record (a leaver!) would wait behind a page.
So a re-link runs after the drain, each page on its own, and a page that fails
stays queued for the next round, still searchable with its old links. Links
are not a permission gate: a link to a leaver names an ID the entity store no
longer answers for.

In memory, like the dead letters: lost when the process stops, and with it
the in-memory stores, which a restart rebuilds from the full listings anyway.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from ekb.process.t6_entities.relink import EntityChange


@dataclass(frozen=True)
class Pending:
    """One page waiting to be processed again, and why (IDs only, no names)."""

    document_key: str
    reasons: tuple[str, ...]
    """``person:sara added``, ``its context headers failed``: what queued it."""
    headers: bool = False
    """Queued for its failed headers: processed again even if its links are right."""


class Relinks:
    """Entity changes not yet looked at, and the pages they (or failed headers) queued."""

    def __init__(self) -> None:
        self._changes: list[EntityChange] = []
        self._pages: dict[str, Pending] = {}
        self._lock = threading.Lock()

    def changed(self, change: EntityChange) -> None:
        """The entity store changed; ignored when no page's links could change."""
        if not change.matters:
            return
        with self._lock:
            self._changes.append(change)

    def take_changes(self) -> list[EntityChange]:
        with self._lock:
            changes, self._changes = self._changes, []
            return changes

    def queue(self, document_key: str, reason: str, *, headers: bool = False) -> None:
        """Queue a page (again): its reasons add up."""
        with self._lock:
            before = self._pages.get(document_key)
            reasons = (*before.reasons, reason) if before is not None else (reason,)
            self._pages[document_key] = Pending(
                document_key,
                tuple(dict.fromkeys(reasons)),
                headers=headers or (before is not None and before.headers),
            )

    def pages(self) -> list[Pending]:
        """The queued pages, by key."""
        with self._lock:
            return [self._pages[key] for key in sorted(self._pages)]

    def done(self, document_key: str) -> None:
        with self._lock:
            self._pages.pop(document_key, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._pages) + len(self._changes)
