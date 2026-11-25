"""T8 option ``source_acl`` (S11): the source's readers as written, and what is worth a look."""

from __future__ import annotations

from ekb.contracts import Acl
from ekb.process.t8_permissions.options.source_acl import SourceAclResolver
from tests.unit.process.t8_permissions.conftest import (
    ALL_STAFF,
    loaded_store,
    membership,
    upsert,
)


def test_the_readers_are_copied_as_the_source_wrote_them() -> None:
    resolution = SourceAclResolver().resolve(upsert(acl=ALL_STAFF), loaded_store())
    assert resolution.acl == ALL_STAFF
    assert resolution.resolver_id == "source_acl@1"
    assert resolution.note == "readers copied from the source: 12 people can read it now"
    assert (resolution.unknown_groups, resolution.unknown_users, resolution.inactive_users) == (
        (),
        (),
        (),
    )


def test_a_group_no_one_is_in_is_kept_and_reported() -> None:
    """Keeping it opens nothing: no one's principals hold it until the group gets members."""
    acl = Acl(groups=frozenset({"hr-compensation", "hr-teem"}))
    resolution = SourceAclResolver().resolve(upsert(acl=acl), loaded_store())
    assert resolution.acl == acl
    assert resolution.unknown_groups == ("hr-teem",)
    assert resolution.note == (
        "readers copied from the source: 1 person can read it now; "
        "no one is in hr-teem (unknown to the identity provider)"
    )


def test_unknown_and_departed_users_are_reported_and_read_nothing() -> None:
    store = loaded_store()
    store.put_membership(membership("olivia", "all-staff", "ca-employees", active=False))
    acl = Acl(users=frozenset({"olivia", "ghost", "bob"}))
    resolution = SourceAclResolver().resolve(upsert(acl=acl), store)
    assert resolution.unknown_users == ("ghost",)
    assert resolution.inactive_users == ("olivia",)
    assert resolution.note == (
        "readers copied from the source: 1 person can read it now; "
        "no identity record for ghost; olivia left: they read nothing"
    )


def test_readers_that_name_nobody_are_copied_and_say_so() -> None:
    resolution = SourceAclResolver().resolve(upsert(acl=Acl()), loaded_store())
    assert resolution.acl == Acl()
    assert (
        resolution.note == "readers copied from the source: it names nobody, so no one can read it"
    )
