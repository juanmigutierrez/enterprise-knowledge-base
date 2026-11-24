"""``record_after``: what the registry remembers after each kind of event."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ekb.changelog.registry.port import DocumentRecord, record_after
from ekb.contracts import Acl
from tests.unit.changelog.factories import AT, acl_change, delete, upsert

ES_ONLY = Acl(groups=frozenset({"es-employees"}))


def test_an_upsert_records_everything_the_event_says() -> None:
    record = record_after(None, upsert("HR-PL-04", "1.1", changed_by="dana"))
    assert record.document_key == "hr_docs:HR-PL-04"
    assert (record.version, record.deleted, record.events) == ("1.1", False, 1)
    assert record.raw_pointer == "mem://ekb-raw/hr_docs/HR-PL-04/1.1"
    assert record.content_hash == "sha256:HR-PL-04-1.1"
    assert record.acl == Acl(groups=frozenset({"all-staff"}))
    assert (record.changed_by, record.updated_at) == ("dana", AT)


def test_an_acl_change_replaces_the_readers_and_keeps_the_content() -> None:
    before = record_after(None, upsert("HR-PL-05", "1.1"))
    after = record_after(before, acl_change("HR-PL-05", "1.2", ES_ONLY, changed_by="lucia"))
    assert (after.version, after.acl, after.changed_by, after.events) == (
        "1.2",
        ES_ONLY,
        "lucia",
        2,
    )
    assert (after.raw_pointer, after.content_hash) == (before.raw_pointer, before.content_hash)


def test_an_acl_change_takes_the_sources_current_hash_when_it_carries_one() -> None:
    before = record_after(None, upsert("HR-PL-05", "1.1"))
    after = record_after(before, acl_change("HR-PL-05", "1.2", ES_ONLY, content_hash="sha256:new"))
    assert after.content_hash == "sha256:new"
    assert after.raw_pointer is None
    assert after.acl == ES_ONLY


def test_an_acl_change_for_an_item_never_seen_records_readers_without_content() -> None:
    record = record_after(None, acl_change("HR-PL-05", "1.2", ES_ONLY))
    assert (record.deleted, record.acl, record.raw_pointer) == (False, ES_ONLY, None)


def test_a_delete_leaves_a_tombstone_with_nothing_about_the_content() -> None:
    alive = record_after(None, upsert("HR-PL-99", "1.1", person_id="alice"))
    gone = record_after(alive, delete("HR-PL-99", "1.1", changed_by="dana"))
    assert (gone.deleted, gone.version, gone.changed_by, gone.events) == (True, "1.1", "dana", 2)
    assert (gone.content_hash, gone.raw_pointer, gone.acl, gone.person_id) == (
        None,
        None,
        Acl(),
        None,
    )


def test_an_acl_change_does_not_bring_a_deleted_item_back() -> None:
    gone = record_after(record_after(None, upsert("HR-PL-99")), delete("HR-PL-99", "1.1"))
    still = record_after(gone, acl_change("HR-PL-99", "1.2", ES_ONLY))
    assert still.deleted
    assert (still.acl, still.raw_pointer, still.events) == (Acl(), None, 3)


def test_an_upsert_after_a_delete_brings_the_item_back() -> None:
    gone = record_after(record_after(None, upsert("HR-PL-11")), delete("HR-PL-11", "1.1"))
    back = record_after(gone, upsert("HR-PL-11", "2.1"))
    assert (back.deleted, back.version, back.events) == (False, "2.1", 3)
    assert back.raw_pointer == "mem://ekb-raw/hr_docs/HR-PL-11/2.1"


@pytest.mark.parametrize(
    "content",
    [
        {"content_hash": "sha256:x"},
        {"raw_pointer": "mem://x"},
        {"acl": Acl(groups=frozenset({"all-staff"}))},
        {"person_id": "alice"},
    ],
)
def test_a_tombstone_that_keeps_any_content_is_refused(content: dict[str, object]) -> None:
    fields: dict[str, object] = {
        "document_key": "hr_docs:HR-PL-99",
        "source": "hr_docs",
        "source_id": "HR-PL-99",
        "version": "1.1",
        "deleted": True,
        "updated_at": AT,
    }
    with pytest.raises(ValidationError, match="deleted record"):
        DocumentRecord.model_validate(fields | content)
