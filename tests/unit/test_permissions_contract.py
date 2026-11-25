"""The permission contract (S11): principals, memberships and the one rule that admits."""

from __future__ import annotations

from datetime import UTC, datetime

from ekb.contracts import Acl, DocumentAcl, Membership, Principals, admits, readers_now


def member(user: str, *groups: str, active: bool = True) -> Membership:
    return Membership(
        user_id=user,
        active=active,
        groups=frozenset(groups),
        source_key=f"identity:users/{user}",
        source_version="1",
    )


def test_a_person_is_admitted_by_name_or_through_a_group_they_are_in_now() -> None:
    alice = Principals(user_id="alice", groups=frozenset({"all-staff", "it-employees"}))
    assert admits(Acl(groups=frozenset({"it-employees", "hr-team"})), alice)
    assert admits(Acl(users=frozenset({"alice"})), alice)
    assert not admits(Acl(groups=frozenset({"hr-team"})), alice)
    assert not admits(Acl(users=frozenset({"bob"})), alice)


def test_readers_that_name_nobody_admit_nobody() -> None:
    assert not admits(Acl(), Principals(user_id="alice", groups=frozenset({"all-staff"})))


def test_an_unknown_or_inactive_person_has_no_principals_and_reads_nothing() -> None:
    """The leaver gate (S1c): an inactive user keeps their groups and still reads nothing."""
    olivia = member("olivia", "all-staff", "ca-employees", active=False)
    assert olivia.groups == {"all-staff", "ca-employees"}
    assert olivia.principals is None
    assert not admits(Acl(groups=frozenset({"all-staff"})), olivia.principals)
    assert not admits(Acl(users=frozenset({"olivia"})), None)


def test_an_active_membership_reads_with_its_user_id_and_groups() -> None:
    assert member("bob", "hr-team").principals == Principals(
        user_id="bob", groups=frozenset({"hr-team"})
    )


def test_readers_now_lists_the_active_people_admitted_sorted() -> None:
    people = (
        member("lucia", "hr-team"),
        member("bob", "hr-team"),
        member("dana", "hr-team", active=False),
        member("alice", "all-staff"),
        member("carla", "es-employees"),
    )
    acl = Acl(groups=frozenset({"hr-team"}), users=frozenset({"carla"}))
    assert readers_now(acl, people) == ("bob", "carla", "lucia")
    assert readers_now(Acl(), people) == ()


def test_a_document_acl_records_the_version_and_time_its_readers_came_from() -> None:
    record = DocumentAcl(
        document_key="hr_docs:HR-PL-05",
        acl=Acl(groups=frozenset({"es-employees"})),
        acl_version="1.2",
        occurred_at=datetime(2026, 11, 25, tzinfo=UTC),
        resolver_id="source_acl@1",
    )
    assert DocumentAcl.model_validate_json(record.model_dump_json()) == record
