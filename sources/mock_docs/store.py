"""The mock docs service's state: pages plus an append-only change feed.

Rules that later steps rely on (they are what "a change" means in S2-S4):

- Every save is a change. A content save raises ``rev``; a permission save
  raises ``acl_rev`` and leaves ``rev`` alone. The source never decides whether
  anything *really* changed: a save with identical text still gets a new
  ``rev`` but keeps the same ``content_hash``. Spotting that is T2's job.
- A delete removes the page and leaves only a ``page.deleted`` entry in the
  feed. Nothing of the content survives, as in a source that empties its bin.
- The feed is ordered by ``seq``, so a reader that keeps the last ``seq`` it
  saw can always catch up with ``changes(since=seq)``.
- Every write may name who made it (``actor``, an identity provider user ID,
  checked against the directory). It is kept on the page as ``modified_by``
  and on the feed entry as ``actor``; seeding names nobody.
- Listeners (the webhook publisher, S2) hear about each change after it is
  saved and after the lock is released, so a listener that calls back into the
  store (as a webhook receiver fetching the page does) cannot deadlock.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from pathlib import Path

from sources.common.clock import Clock, utc_now
from sources.common.directory import Directory
from sources.common.feed import ChangeFeed
from sources.common.seed import DATA_DIR
from sources.mock_docs.models import (
    ChangeKind,
    Page,
    PageAcl,
    PageCreate,
    PageEdit,
    PageSummary,
    SourceChange,
)
from sources.mock_docs.seed import load_pages

DEFAULT_DATA_DIR = DATA_DIR

Listener = Callable[[SourceChange], None]


class PageNotFoundError(KeyError):
    """No page with this ID (it never existed or was deleted)."""


class PageExistsError(ValueError):
    """A page with this ID already exists."""


def content_hash(title: str, body: str) -> str:
    """Fingerprint of what a reader sees. Same text, same hash, whatever the rev."""
    digest = hashlib.sha256(f"{title}\n{body}".encode()).hexdigest()
    return f"sha256:{digest}"


class DocsStore:
    """In-memory pages and change feed, safe to use from several threads."""

    def __init__(self, directory: Directory, *, clock: Clock = utc_now) -> None:
        self.directory = directory
        self._clock = clock
        self._pages: dict[str, Page] = {}
        self._deleted_revs: dict[str, int] = {}
        self._feed: ChangeFeed[SourceChange] = ChangeFeed()
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()

    @classmethod
    def from_seed(cls, data_dir: Path = DEFAULT_DATA_DIR, *, clock: Clock = utc_now) -> DocsStore:
        """A store holding the synthetic documents, each recorded as created."""
        store = cls(Directory.load(data_dir / "directory.yaml"), clock=clock)
        for request in load_pages(data_dir / "hr_docs"):
            store.create(request)
        return store

    def add_listener(self, listener: Listener) -> None:
        """Call ``listener`` with every change from now on (not with past ones)."""
        with self._lock:
            self._listeners.append(listener)

    # ------------------------------------------------------------------ reads

    @property
    def cursor(self) -> int:
        """``seq`` of the latest change (0 when the feed is empty)."""
        with self._lock:
            return self._feed.cursor

    def list_pages(self) -> list[PageSummary]:
        with self._lock:
            pages = sorted(self._pages.values(), key=lambda p: p.id)
        return [
            PageSummary.model_validate(p.model_dump(exclude={"body", "first_rev"})) for p in pages
        ]

    def get(self, page_id: str) -> Page:
        with self._lock:
            return self._get(page_id)

    def changes(self, since: int = 0) -> list[SourceChange]:
        """Changes with ``seq`` greater than ``since``, oldest first."""
        with self._lock:
            return self._feed.since(since)

    def read_feed(self, since: int) -> tuple[list[SourceChange], int]:
        """Read entries and their resume cursor in the same snapshot."""
        with self._lock:
            return self._feed.since(since), self._feed.cursor

    # ----------------------------------------------------------------- writes

    def create(self, request: PageCreate, *, actor: str | None = None) -> Page:
        self._check_actor(actor)
        self.directory.check(users=request.acl.users, groups=request.acl.groups)
        with self._lock:
            if request.id in self._pages:
                raise PageExistsError(f"page {request.id} exists")
            first_rev = self._deleted_revs.pop(request.id, 0) + 1
            page = Page(
                **request.model_dump(exclude={"acl"}),
                acl=request.acl,
                first_rev=first_rev,
                rev=first_rev,
                acl_rev=1,
                content_hash=content_hash(request.title, request.body),
                updated_at=self._clock(),
                modified_by=actor,
            )
            self._pages[page.id] = page
            change = self._record(ChangeKind.CREATED, page, actor=page.modified_by)
        self._notify(change)
        return page

    def edit(self, page_id: str, request: PageEdit, *, actor: str | None = None) -> Page:
        """Save new content. Always a new ``rev``, even if the text is identical."""
        self._check_actor(actor)
        with self._lock:
            current = self._get(page_id)
            title = request.title or current.title
            page = current.model_copy(
                update={
                    "title": title,
                    "body": request.body,
                    "rev": current.rev + 1,
                    "content_hash": content_hash(title, request.body),
                    "updated_at": self._clock(),
                    "modified_by": actor,
                }
            )
            self._pages[page_id] = page
            change = self._record(ChangeKind.UPDATED, page, actor=page.modified_by)
        self._notify(change)
        return page

    def set_acl(self, page_id: str, acl: PageAcl, *, actor: str | None = None) -> Page:
        """Replace who may read the page. Content and ``rev`` stay as they are."""
        self._check_actor(actor)
        self.directory.check(users=acl.users, groups=acl.groups)
        with self._lock:
            current = self._get(page_id)
            page = current.model_copy(
                update={
                    "acl": acl,
                    "acl_rev": current.acl_rev + 1,
                    "updated_at": self._clock(),
                    "modified_by": actor,
                }
            )
            self._pages[page_id] = page
            change = self._record(ChangeKind.PERMISSIONS_CHANGED, page, actor=page.modified_by)
        self._notify(change)
        return page

    def delete(self, page_id: str, *, actor: str | None = None) -> SourceChange:
        self._check_actor(actor)
        with self._lock:
            page = self._get(page_id)
            self._deleted_revs[page_id] = page.rev
            del self._pages[page_id]
            change = self._record(ChangeKind.DELETED, page, actor=actor)
        self._notify(change)
        return change

    # --------------------------------------------------------------- helpers

    def _get(self, page_id: str) -> Page:
        try:
            return self._pages[page_id]
        except KeyError:
            raise PageNotFoundError(f"page {page_id} not found") from None

    def _notify(self, change: SourceChange) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            listener(change)

    def _check_actor(self, actor: str | None) -> None:
        if actor is not None:
            self.directory.check(users=[actor])

    def _record(self, kind: ChangeKind, page: Page, *, actor: str | None) -> SourceChange:
        at = self._clock()
        return self._feed.append(
            lambda seq: SourceChange(
                seq=seq,
                event=kind,
                page_id=page.id,
                rev=page.rev,
                acl_rev=page.acl_rev,
                at=at,
                actor=actor,
            )
        )
