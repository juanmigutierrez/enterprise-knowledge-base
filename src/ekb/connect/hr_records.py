"""Normalize fetched HR records for both polling connectors."""

from __future__ import annotations

from ekb.connect.hr_api import HrRecord
from ekb.connect.normalize import hr_api as normalize
from ekb.connect.normalize.common import canonical_json
from ekb.connect.people import PersonMap
from ekb.connect.port import OriginalStore
from ekb.contracts import ChangeEvent


def record_to_event(
    record: HrRecord, *, source: str, store_original: OriginalStore, people: PersonMap
) -> ChangeEvent:
    """Shared by the delta and the full listing: one record → one upsert."""
    original = canonical_json(record.data)
    version = normalize.version_of(record)
    pointer = store_original(f"{source}:{record.source_id}", version, original)
    person = (
        people.for_employee(record.id, name=record.name)
        if record.collection == "employees"
        else None
    )
    return normalize.record_event(
        source,
        record,
        raw_pointer=pointer,
        original=original,
        person_id=person,
        changed_by=people.user(record.updated_by),
    )
