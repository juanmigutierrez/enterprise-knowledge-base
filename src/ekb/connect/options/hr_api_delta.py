"""Delta-polling connector for the HR record API: the pull half of D3.

Record systems push nothing; you ask "what changed since time X?". Each poll
passes back the ``as_of`` of the previous answer as ``updated_since``. The
first poll has no cursor, so it reads every record: the initial load.

The filter is inclusive, so a record can come back twice. That is harmless:
the repeat has the same version and hash, and T2 skips it. What the delta can
never show is a **deleted** record; that is ``hr_api_full_listing``'s job.

The cursor lives in memory for now; from S3 on it is stored with the change
log, so a restarted connector resumes where it stopped.
"""

from __future__ import annotations

from datetime import datetime

from ekb.connect.hr_api import COLLECTIONS, Collection, HrApi
from ekb.connect.hr_records import record_to_event
from ekb.connect.people import PersonMap
from ekb.connect.port import EventSink, OriginalStore
from ekb.platform.registry import register


@register("connect", "hr_api_delta")
class HrApiDeltaConnector:
    def __init__(
        self,
        api: HrApi,
        sink: EventSink,
        store_original: OriginalStore,
        people: PersonMap,
        source: str = "hr_api",
    ) -> None:
        self._api = api
        self._sink = sink
        self._store_original = store_original
        self._people = people
        self._source = source
        self._since: dict[Collection, datetime | None] = dict.fromkeys(COLLECTIONS)

    @property
    def source(self) -> str:
        return self._source

    @property
    def cursors(self) -> dict[Collection, datetime | None]:
        return dict(self._since)

    def sync(self) -> int:
        emitted = 0
        for collection in COLLECTIONS:
            listing = self._api.list(collection, updated_since=self._since[collection])
            for record in listing.records:
                self._sink(
                    record_to_event(
                        record,
                        source=self._source,
                        store_original=self._store_original,
                        people=self._people,
                    )
                )
                emitted += 1
            # Moved only after every record is emitted: a failure re-reads, never skips.
            self._since[collection] = listing.as_of
        return emitted
