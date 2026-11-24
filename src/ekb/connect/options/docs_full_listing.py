"""Full-listing connector for the docs tool: the safety net of D3.

Webhooks are best-effort: a receiver that is down misses notifications, and the
source does not resend them. A full listing does not depend on anything having
been delivered. It asks the source for every page it has now (IDs, versions,
hashes; no bodies, so it is cheap) and compares that with what the knowledge
base already knows:

========================== =================================================
in the listing, not known  new → fetch, ``upsert``
known, ``rev`` differs     content changed → fetch, ``upsert``
known, only ``acl_rev``    permissions changed → ``acl_change`` (no fetch)
known, same version        nothing
known, not in the listing  **gone → ``delete``**, the case nothing else catches
========================== =================================================

A delete found this way has no ``changed_by``: the source never said who.
Every other person ID goes through the :class:`PersonMap`.

"What is known" is injected (``known``): from S3 on it is the document
registry; in S2's demo it is built from the events already emitted.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from ekb.connect.docs_api import DocsApi, DocsPageSummary
from ekb.connect.normalize import docs as normalize
from ekb.connect.people import PersonMap
from ekb.connect.port import EventSink, OriginalStore
from ekb.contracts import ChangeEvent
from ekb.platform.registry import register

Known = Callable[[], Mapping[str, str]]
"""``source_id -> version`` of every document the knowledge base holds for this source."""


def _now() -> datetime:
    return datetime.now(UTC)


@register("connect", "docs_full_listing")
class DocsFullListingConnector:
    def __init__(
        self,
        api: DocsApi,
        sink: EventSink,
        store_original: OriginalStore,
        people: PersonMap,
        known: Known,
        source: str = "hr_docs",
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._api = api
        self._sink = sink
        self._store_original = store_original
        self._people = people
        self._known = known
        self._source = source
        self._clock = clock

    @property
    def source(self) -> str:
        return self._source

    def sync(self) -> int:
        events = self.diff()
        for event in events:
            self._sink(event)
        return len(events)

    def diff(self) -> list[ChangeEvent]:
        """The events that would bring the knowledge base level with the source."""
        unseen = dict(self._known())
        events: list[ChangeEvent] = []
        for summary in self._api.list_pages():
            known_version = unseen.pop(summary.id, None)
            event = self._compare(summary, known_version)
            if event is not None:
                events.append(event)
        # A page we know that the source no longer lists has been deleted. The
        # source never said so; the listing is the only evidence (D3).
        for source_id, version in sorted(unseen.items()):
            events.append(self._deleted(source_id, version))
        return events

    def _compare(self, summary: DocsPageSummary, known_version: str | None) -> ChangeEvent | None:
        known = normalize.parse_version(known_version) if known_version else None
        if known == (summary.rev, summary.acl_rev):
            return None
        if known is not None and known[0] == summary.rev:
            return normalize.acl_change_event(
                self._source,
                summary,
                acl=normalize.acl_of(summary.acl, users=self._people.users_in(summary.acl.users)),
                changed_by=self._people.user(summary.modified_by),
            )
        return self._fetch_upsert(summary, known_version)

    def _fetch_upsert(self, summary: DocsPageSummary, known_version: str | None) -> ChangeEvent:
        fetched = self._api.get_page(summary.id)
        if fetched is None:  # deleted between the listing and the fetch
            version = known_version or normalize.version_of(summary.rev, summary.acl_rev)
            return self._deleted(summary.id, version)
        page = fetched.page
        version = normalize.version_of(page.rev, page.acl_rev)
        pointer = self._store_original(f"{self._source}:{page.id}", version, fetched.original)
        return normalize.upsert_event(
            self._source,
            page,
            raw_pointer=pointer,
            acl=normalize.acl_of(page.acl, users=self._people.users_in(page.acl.users)),
            changed_by=self._people.user(page.modified_by),
        )

    def _deleted(self, source_id: str, version: str) -> ChangeEvent:
        # The source gives no deletion time; the time we noticed is the best we have.
        return normalize.delete_event(
            self._source, source_id, version=version, occurred_at=self._clock()
        )
