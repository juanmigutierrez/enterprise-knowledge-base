"""The permission store's memberships kept current from identity events (S11)."""

from __future__ import annotations

import pytest

from ekb.changelog.rawstore.options.in_memory import InMemoryRawStore
from ekb.contracts import ChangeEvent, ChangeOp
from ekb.process.t8_permissions.records import idp_user
from ekb.process.t8_permissions.sync import MembershipSync
from ekb.stores.permissions.options.in_memory import InMemoryPermissionStore
from tests.unit.process.t8_permissions.conftest import user_record
from tests.unit.process.t8_permissions.test_records import event


def setup() -> tuple[MembershipSync, InMemoryPermissionStore, InMemoryRawStore]:
    store, raw = InMemoryPermissionStore(), InMemoryRawStore()
    return MembershipSync(store, raw, {"identity": idp_user}), store, raw


def upsert(
    raw: InMemoryRawStore, user: str, *groups: str, active: bool = True, v: str = "1"
) -> ChangeEvent:
    base = event(f"users/{user}", version=v)
    pointer = raw.put(base.document_key, v, user_record(user, *groups, active=active))
    return base.model_copy(update={"raw_pointer": pointer})


def test_a_user_record_puts_their_membership_and_a_purge_removes_it() -> None:
    sync, store, raw = setup()
    result = sync.handle(upsert(raw, "alice", "all-staff", "it-employees"))
    assert result.reason == "alice added: all-staff, it-employees"
    assert store.membership("alice") == result.membership
    assert sync.forget("identity:users/alice") == result.membership
    assert store.membership("alice") is None
    assert sync.forget("identity:users/alice") is None


def test_a_group_change_replaces_the_membership_and_says_what_changed() -> None:
    sync, store, raw = setup()
    sync.handle(upsert(raw, "alice", "all-staff", "it-employees"))
    result = sync.handle(upsert(raw, "alice", "all-staff", "hr-team", v="2"))
    assert result.replaced is not None
    assert result.reason == "alice: +hr-team -it-employees"
    assert store.principals("alice") is not None
    assert store.membership("alice") == result.membership


def test_a_leaver_is_kept_inactive_and_reads_nothing() -> None:
    sync, store, raw = setup()
    sync.handle(upsert(raw, "olivia", "all-staff", "ca-employees"))
    result = sync.handle(upsert(raw, "olivia", "all-staff", "ca-employees", active=False, v="2"))
    assert result.reason == "olivia left: reads nothing now (keeps 2 groups)"
    assert store.principals("olivia") is None
    back = sync.handle(upsert(raw, "olivia", "all-staff", v="3"))
    assert back.reason == "olivia is back: reads with all-staff"


def test_only_identity_upserts_are_handled() -> None:
    sync, _, _ = setup()
    assert sync.handles("identity")
    assert not sync.handles("hr_docs")
    with pytest.raises(ValueError, match="defines no memberships"):
        sync.handle(event().model_copy(update={"source": "hr_docs"}))
    delete = ChangeEvent(
        source="identity",
        source_id="users/alice",
        op=ChangeOp.DELETE,
        version="9",
        occurred_at=event().occurred_at,
    )
    with pytest.raises(ValueError, match="only an upsert"):
        sync.handle(delete)
