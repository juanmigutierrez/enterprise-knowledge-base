"""Full-listing connector for the HR record API: the only way to see a delete.

The HR API deletes records without a trace: no event, no tombstone, nothing in
any delta (S1b). So the connector lists every record and compares the listing
with what the knowledge base knows (``known``: ``source_id -> version``):

=============================  ===================================
compared with what is known    event
=============================  ===================================
new, or ``updated_at`` differs ``upsert`` (the listing has the record)
same version                   nothing, unless the person mapping
                               arrived since (below)
known, **not listed**          ``delete``: who deleted is unknown
=============================  ===================================

Unlike the docs listing, no fetch is needed: an HR listing carries the full
records.

**An employee who maps now is sent again (S16).** HR usually hires before the
identity provider has an account. The hire's first event then carries no
person (fail closed, D3), and since the record does not change when the
account appears, no version comparison would ever send it again: the person
would never become an entity. So the listing reloads the person map when it
reported an employee before, and sends the same version again for each one
that now has an identity user. T2 sees "same content, now about someone" and
processes it (S4); the entity store gets the person, and the worker re-links
the pages that name them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from ekb.connect.hr_api import COLLECTIONS, HrApi
from ekb.connect.hr_records import record_to_event
from ekb.connect.normalize import hr_api as normalize
from ekb.connect.normalize.common import delete_event
from ekb.connect.people import PersonMap
from ekb.connect.port import EventSink, OriginalStore
from ekb.contracts import ChangeEvent
from ekb.contracts.listing import ListingVersion, NeedsOriginal
from ekb.platform.registry import register

Known = Callable[[], Mapping[str, ListingVersion]]


def _now() -> datetime:
    return datetime.now(UTC)


@register("connect", "hr_api_full_listing")
class HrApiFullListingConnector:
    def __init__(
        self,
        api: HrApi,
        sink: EventSink,
        store_original: OriginalStore,
        people: PersonMap,
        known: Known,
        source: str = "hr_api",
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
            if event.person_id is not None and event.source_id.startswith("employees/"):
                employee_id = event.source_id.removeprefix("employees/")
                self._people.settle(f"employee:{employee_id}")
        return len(events)

    def diff(self) -> list[ChangeEvent]:
        unseen = dict(self._known())
        reported = {u for u in self._people.unmapped if u.startswith("employee:")}
        if reported:
            self._people.refresh()  # an identity account may have appeared since
        events: list[ChangeEvent] = []
        for collection in COLLECTIONS:
            for record in self._api.list(collection).records:
                if unseen.pop(record.source_id, None) == normalize.version_of(record):
                    label = f"employee:{record.id}"
                    if (
                        record.collection != "employees"
                        or label not in reported
                        or self._people.maps_now(record.id, record.name) is None
                    ):
                        continue
                events.append(
                    record_to_event(
                        record,
                        source=self._source,
                        store_original=self._store_original,
                        people=self._people,
                    )
                )
        for source_id, version in sorted(unseen.items()):
            events.append(
                delete_event(
                    self._source,
                    source_id,
                    version=version.version if isinstance(version, NeedsOriginal) else version,
                    occurred_at=self._clock(),
                )
            )
        return events
