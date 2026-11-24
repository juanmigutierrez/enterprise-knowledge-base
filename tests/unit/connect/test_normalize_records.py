"""Shared T1 rules for structured records."""

from __future__ import annotations

from datetime import UTC, datetime

from ekb.connect.normalize import identity
from ekb.connect.normalize.common import canonical_json, content_hash, delete_event
from ekb.contracts import Acl


def test_the_same_record_always_gives_the_same_bytes_and_hash() -> None:
    a = canonical_json({"b": 1, "a": "è", "c": None})
    b = canonical_json({"c": None, "a": "è", "b": 1})
    assert a == b == '{"a":"è","b":1,"c":null}'.encode()
    assert content_hash(a) == content_hash(b)
    assert content_hash(a).startswith("sha256:")


def test_a_delete_names_only_who_deleted() -> None:
    event = delete_event(
        "hr_db",
        "office_headcount/BLQ",
        version="18",
        occurred_at=datetime.now(UTC),
        changed_by="dana",
    )
    assert (event.acl, event.person_id, event.raw_pointer, event.content_hash) == (
        Acl(),
        None,
        None,
        None,
    )
    assert event.changed_by == "dana"


def test_identity_items_are_named_per_user() -> None:
    assert identity.source_id_of("alice") == "users/alice"
    assert {"user.created", "group.member_removed"} <= identity.KINDS
