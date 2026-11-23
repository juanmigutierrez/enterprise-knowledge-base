"""The identity provider: group changes are permission changes, leavers are deactivated."""

from __future__ import annotations

import pytest
from sources.mock_idp.models import IdentityEventKind, UserCreate
from sources.mock_idp.store import (
    IdentityConflictError,
    IdpStore,
    NotFoundError,
    UnknownPrincipalError,
)

from tests.sources.conftest import TickingClock


@pytest.fixture
def store(ticking_clock: TickingClock) -> IdpStore:
    return IdpStore.from_seed(clock=ticking_clock)


def test_seed_is_the_directory_and_the_feed_starts_empty(store: IdpStore) -> None:
    assert len(store.users()) == 12
    assert len(store.groups()) == 6
    assert all(u.active for u in store.users())
    assert store.events() == []
    assert store.cursor == 0


def test_groups_and_users_agree(store: IdpStore) -> None:
    for group in store.groups():
        for user_id in group.members:
            assert group.id in store.user(user_id).groups
    assert store.group("hr-compensation").members == ["bob"]
    assert store.group("hr-team").members == ["bob", "dana", "lucia"]


def test_removing_a_member_is_one_event_and_changes_their_groups(store: IdpStore) -> None:
    before = store.user("alice").groups
    store.remove_member("it-employees", "alice")
    assert store.user("alice").groups == [g for g in before if g != "it-employees"]
    [event] = store.events()
    assert (event.event, event.user_id, event.group) == (
        IdentityEventKind.MEMBER_REMOVED,
        "alice",
        "it-employees",
    )


def test_adding_a_member(store: IdpStore) -> None:
    group = store.add_member("hr-team", "alice")
    assert "alice" in group.members
    assert [e.event for e in store.events()] == [IdentityEventKind.MEMBER_ADDED]


def test_no_op_membership_changes_emit_nothing(store: IdpStore) -> None:
    store.add_member("all-staff", "alice")  # already a member
    store.remove_member("hr-team", "alice")  # not a member
    assert store.events() == []


def test_leaver_is_deactivated_not_deleted(store: IdpStore) -> None:
    user = store.deactivate("olivia")
    assert user.active is False
    assert "olivia" in {u.id for u in store.users()}
    assert "ca-employees" in user.groups  # memberships kept: S11 must ignore inactive users
    assert [e.event for e in store.events()] == [IdentityEventKind.USER_DEACTIVATED]
    with pytest.raises(IdentityConflictError, match="already inactive"):
        store.deactivate("olivia")
    assert store.cursor == 1


def test_joiner_emits_created_and_one_event_per_group(store: IdpStore) -> None:
    user = store.create_user(
        UserCreate(
            id="sofia", name="Sofia Marino", location="Milan", groups=["it-employees", "all-staff"]
        )
    )
    assert user.groups == ["all-staff", "it-employees"]
    events = store.events()
    assert [(e.event, e.group) for e in events] == [
        (IdentityEventKind.USER_CREATED, None),
        (IdentityEventKind.MEMBER_ADDED, "all-staff"),
        (IdentityEventKind.MEMBER_ADDED, "it-employees"),
    ]
    assert [e.seq for e in events] == [1, 2, 3]


@pytest.mark.parametrize(
    ("request_fields", "error", "message"),
    [
        ({"id": "alice", "name": "Someone Else"}, IdentityConflictError, "exists"),
        ({"id": "alice2", "name": "Alice Rossi"}, IdentityConflictError, "1-1"),
        (
            {"id": "sofia", "name": "Sofia Marino", "groups": ["no-such-group"]},
            UnknownPrincipalError,
            "no-such",
        ),
    ],
)
def test_bad_joiners_are_refused_and_nothing_is_recorded(
    store: IdpStore, request_fields: dict[str, object], error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        store.create_user(UserCreate.model_validate({"location": "Milan"} | request_fields))
    assert len(store.users()) == 12
    assert store.events() == []


def test_unknown_users_and_groups_are_not_found(store: IdpStore) -> None:
    with pytest.raises(NotFoundError, match="mallory"):
        store.user("mallory")
    with pytest.raises(NotFoundError, match="mallory"):
        store.add_member("hr-team", "mallory")
    with pytest.raises(NotFoundError, match="admins"):
        store.remove_member("admins", "alice")
    with pytest.raises(NotFoundError):
        store.deactivate("mallory")


def test_event_feed_is_ordered_and_resumable(store: IdpStore) -> None:
    store.remove_member("it-employees", "alice")
    store.add_member("hr-team", "carla")
    store.deactivate("olivia")
    events = store.events()
    assert [e.seq for e in events] == [1, 2, 3]
    assert [e.at for e in events] == sorted(e.at for e in events)
    assert store.events(since=2) == events[2:]
    assert store.events(since=store.cursor) == []
