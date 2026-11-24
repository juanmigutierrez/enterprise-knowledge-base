"""The in-memory raw store: exact bytes back, immutable versions, purge on delete."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from ekb.changelog.rawstore.options.in_memory import InMemoryRawStore
from ekb.changelog.rawstore.port import ConflictingOriginalError, OriginalNotFoundError

AT = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
PAGE = b'{"id":"HR-PL-04","body":"# Remote work, Milan\\n"}'


@pytest.fixture
def store() -> InMemoryRawStore:
    return InMemoryRawStore(clock=lambda: AT)


def test_an_original_comes_back_byte_for_byte(store: InMemoryRawStore) -> None:
    pointer = store.put("hr_docs:HR-PL-04", "1.1", PAGE)
    assert pointer == "mem://ekb-raw/hr_docs/HR-PL-04/1.1"
    assert store.get(pointer) == PAGE


def test_ids_and_versions_with_slashes_and_colons_are_encoded_and_never_collide(
    store: InMemoryRawStore,
) -> None:
    record = store.put("hr_api:employees/E-1001", "2026-11-24T09:00:00+00:00", b"{}")
    assert record == ("mem://ekb-raw/hr_api/employees%2FE-1001/2026-11-24T09%3A00%3A00%2B00%3A00")
    a = store.put("hr_db:a/b", "c", b"first")
    b = store.put("hr_db:a", "b/c", b"second")
    assert a != b
    assert (store.get(a), store.get(b)) == (b"first", b"second")


def test_storing_the_same_bytes_again_is_harmless(store: InMemoryRawStore) -> None:
    first = store.put("hr_docs:HR-PL-04", "1.1", PAGE)
    assert store.put("hr_docs:HR-PL-04", "1.1", PAGE) == first
    assert len(store.versions("hr_docs:HR-PL-04")) == 1


def test_different_bytes_for_a_stored_version_are_refused(store: InMemoryRawStore) -> None:
    pointer = store.put("hr_docs:HR-PL-04", "1.1", PAGE)
    with pytest.raises(ConflictingOriginalError, match="already stored with other bytes"):
        store.put("hr_docs:HR-PL-04", "1.1", b"something else")
    assert store.get(pointer) == PAGE  # the stored original is untouched


def test_versions_are_listed_oldest_first_with_size_and_checksum(store: InMemoryRawStore) -> None:
    store.put("hr_docs:HR-PL-04", "1.1", PAGE)
    store.put("hr_docs:HR-PL-04", "2.1", PAGE + b" ")
    first, second = store.versions("hr_docs:HR-PL-04")
    assert (first.version, second.version) == ("1.1", "2.1")
    assert (first.size, first.stored_at) == (len(PAGE), AT)
    assert first.checksum == f"sha256:{hashlib.sha256(PAGE).hexdigest()}"
    assert store.versions("hr_docs:HR-PL-05") == []


def test_purge_removes_every_version_of_one_item_only(store: InMemoryRawStore) -> None:
    old = store.put("hr_docs:HR-PL-99", "1.1", b"travel 2024")
    new = store.put("hr_docs:HR-PL-99", "2.1", b"travel 2024, revised")
    kept = store.put("hr_docs:HR-PL-04", "1.1", PAGE)
    assert store.purge("hr_docs:HR-PL-99") == 2
    for pointer in (old, new):
        with pytest.raises(OriginalNotFoundError, match="purged"):
            store.get(pointer)
    assert store.versions("hr_docs:HR-PL-99") == []
    assert store.get(kept) == PAGE
    assert store.purge("hr_docs:HR-PL-99") == 0


def test_an_unknown_pointer_is_not_found(store: InMemoryRawStore) -> None:
    with pytest.raises(OriginalNotFoundError):
        store.get("mem://ekb-raw/hr_docs/HR-PL-04/9.9")
