"""Reading identity user records as memberships (S11)."""

from __future__ import annotations

import pytest

from ekb.contracts import ChangeEvent, ChangeOp
from ekb.process.t8_permissions.records import RecordError, idp_user
from tests.unit.process.t8_permissions.conftest import ALL_STAFF, NOW, membership, user_record


def event(source_id: str = "users/alice", version: str = "3") -> ChangeEvent:
    return ChangeEvent(
        source="identity",
        source_id=source_id,
        op=ChangeOp.UPSERT,
        version=version,
        occurred_at=NOW,
        acl=ALL_STAFF,
        raw_pointer=f"mem://raw/identity:{source_id}@{version}",
        person_id=source_id.partition("/")[2] or None,
    )


def test_a_user_record_is_their_membership_with_the_version_it_came_from() -> None:
    read = idp_user(event(), user_record("alice", "all-staff", "it-employees"))
    assert read == membership("alice", "all-staff", "it-employees", version="3")


def test_a_leaver_keeps_their_groups_and_is_inactive() -> None:
    read = idp_user(event("users/olivia"), user_record("olivia", "all-staff", active=False))
    assert not read.active
    assert read.groups == {"all-staff"}
    assert read.principals is None


def test_a_record_for_someone_else_or_not_a_user_or_not_a_record_is_refused() -> None:
    with pytest.raises(RecordError, match="the stored record is bob, not alice"):
        idp_user(event(), user_record("bob"))
    with pytest.raises(RecordError, match="users/<id> records"):
        idp_user(event("groups/hr-team"), user_record("alice"))
    with pytest.raises(RecordError, match="not an identity user record"):
        idp_user(event(), b'{"id": "alice"}')
