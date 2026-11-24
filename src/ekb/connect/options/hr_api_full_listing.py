"""Full-listing connector for the HR record API: the only way to see a delete.

The HR API deletes records without a trace: no event, no tombstone, nothing in
any delta (S1b). So the connector lists every record and compares the listing
with what the knowledge base knows (``known``: ``source_id -> version``):

=============================  ===================================
compared with what is known    event
=============================  ===================================
new, or ``updated_at`` differs ``upsert`` (the listing has the record)
same version                   nothing
known, **not listed**          ``delete``: who deleted is unknown
=============================  ===================================

Unlike the docs listing, no fetch is needed: an HR listing carries the full
records.
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
from ekb.platform.registry import register

Known = Callable[[], Mapping[str, str]]


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
        return len(events)

    def diff(self) -> list[ChangeEvent]:
        unseen = dict(self._known())
        events: list[ChangeEvent] = []
        for collection in COLLECTIONS:
            for record in self._api.list(collection).records:
                if unseen.pop(record.source_id, None) == normalize.version_of(record):
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
                delete_event(self._source, source_id, version=version, occurred_at=self._clock())
            )
        return events
