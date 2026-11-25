"""T8 behind T7 … T2 (S11): nothing searchable before T8, the fast lane, group changes,
leavers and deletes, end to end on an in-memory change log."""

from __future__ import annotations

import pytest

from ekb.app.changelog import ChangeLog
from ekb.app.chunk import Processed, chunk_after_t3, chunk_step
from ekb.app.classify import classify_step, forgetter
from ekb.app.diff import diff_step
from ekb.app.embed import embed_step
from ekb.app.parse import DOCUMENT_SOURCES, parse_after_t2, parse_step
from ekb.app.permissions import (
    membership_sync,
    permission_step,
    permission_store,
    readers_after_t2,
    resolver,
    search_store,
    unpublisher,
)
from ekb.contracts import Acl, ChangeEvent, DiffAction
from ekb.platform.gateway.errors import NoModelAvailableError
from ekb.platform.registry import UnknownOptionError
from ekb.process.t2_diff.step import DiffStep
from ekb.process.t8_permissions.step import PermissionResult, T8Action, T8Error
from tests.unit.changelog.factories import acl_change, delete, upsert
from tests.unit.platform.gateway.helpers import make_world
from tests.unit.process.t3_parse.conftest import page_json
from tests.unit.process.t8_permissions.conftest import user_record

PAGE = "hr_docs:HR-PL-01"
BODY = (
    "# Annual leave\n\n## Carrying leave over\nUp to 5 unused days move to January.\n\n"
    "## Requests\nAsk your manager two weeks ahead.\n"
)
IT_HR = Acl(groups=frozenset({"it-employees", "hr-team"}))
HR = Acl(groups=frozenset({"hr-team"}))
USERS = {
    "alice": ("all-staff", "it-employees"),
    "bob": ("all-staff", "it-employees", "hr-team"),
    "carla": ("all-staff", "es-employees"),
}


class World:
    """T2 to T8 on an in-memory change log; T7 on the fake model behind the gateway."""

    def __init__(self, *, t7: bool = True) -> None:
        self.changelog = ChangeLog.in_memory()
        self.models = make_world()
        self.client = self.models.embedders["fake:vec"]
        self.permissions = permission_store(current_lifetime=self.changelog.current_lifetime)
        self.index = search_store(current_lifetime=self.changelog.current_lifetime)
        self.t8 = permission_step(self.permissions, self.index)
        self.processed: list[Processed] = []
        self.fast: list[PermissionResult] = []
        members = membership_sync(self.changelog, self.permissions)
        then = chunk_after_t3(
            chunk_step(),
            self.processed.append,
            classify_step(),
            t7=embed_step("model", self.models.gateway) if t7 else None,
            t8=self.t8,
            memberships=members,
        )
        forget = forgetter(self.models.gateway, unpublisher(self.index, self.permissions, members))
        parse = parse_after_t2(parse_step(self.changelog), then, forget)
        self.t2: DiffStep = diff_step(
            self.changelog, readers_after_t2(self.t8, parse, DOCUMENT_SOURCES, self.fast.append)
        )
        for user, groups in USERS.items():
            self.handle(self.user(user, *groups))

    def page(self, version: str = "1.1", body: str = BODY, acl: Acl = IT_HR) -> ChangeEvent:
        pointer = self.changelog.store_original(PAGE, version, page_json(body))
        return upsert("HR-PL-01", version, raw_pointer=pointer, acl=acl)

    def user(self, user_id: str, *groups: str, active: bool = True, v: str = "1") -> ChangeEvent:
        key = f"identity:users/{user_id}"
        pointer = self.changelog.store_original(
            key, v, user_record(user_id, *groups, active=active)
        )
        return upsert(
            f"users/{user_id}",
            v,
            source="identity",
            raw_pointer=pointer,
            person_id=user_id,
            content_hash=f"sha256:{user_id}-{v}-{active}-{'-'.join(groups)}",
        )

    def handle(self, *events: ChangeEvent) -> list[DiffAction]:
        return [self.t2.handle(event).decision.action for event in events]

    def sees(self, user: str) -> int:
        """How many published chunks ``user`` would find now."""
        return len(self.index.readable_by(self.permissions.principals(user)))


def test_every_chunk_is_published_with_the_page_readers_and_only_readers_find_it() -> None:
    world = World()
    assert world.handle(world.page()) == [DiffAction.PROCESS]

    published = world.index.chunks(PAGE)
    assert len(published) > 1
    for chunk in published:
        assert chunk.publishable
        assert (chunk.acl, chunk.acl_version, chunk.resolver_id) == (IT_HR, "1.1", "source_acl@1")
    assert world.processed[-1].final == published
    assert world.sees("alice") == world.sees("bob") == len(published)
    assert world.sees("carla") == 0
    assert world.sees("nobody") == 0


def test_identity_records_fill_the_memberships_and_are_never_published() -> None:
    world = World()
    assert [m.user_id for m in world.permissions.memberships()] == ["alice", "bob", "carla"]
    assert world.index.chunks() == ()
    assert world.permissions.all_readers() == ()  # a record has no readers record either
    membership = world.processed[0].membership
    assert membership is not None
    assert membership.reason == "alice added: all-staff, it-employees"


def test_without_t7_nothing_is_published_and_the_event_comes_back() -> None:
    """The publication gate in the wiring: no vector, no publication."""
    world = World(t7=False)
    with pytest.raises(T8Error, match="no vector: T7 runs before T8"):
        world.t2.handle(world.page())
    assert world.index.chunks() == ()
    assert world.changelog.registry.get(PAGE) is None


def test_a_group_change_takes_effect_with_no_chunk_rewritten_or_embedded() -> None:
    world = World()
    world.handle(world.page())
    before, calls = world.index.chunks(), world.client.calls
    assert world.sees("alice") == len(before)

    assert world.handle(world.user("alice", "all-staff", v="2")) == [DiffAction.PROCESS]

    assert world.sees("alice") == 0  # it-employees was her only way in
    assert world.index.chunks() == before
    assert world.client.calls == calls


def test_a_leaver_reads_nothing_though_they_keep_their_groups() -> None:
    world = World()
    world.handle(world.page())
    world.handle(world.user("bob", *USERS["bob"], active=False, v="2"))
    membership = world.permissions.membership("bob")
    assert membership is not None
    assert membership.groups == set(USERS["bob"])
    assert world.sees("bob") == 0


def test_the_fast_lane_changes_the_readers_in_place_and_embeds_nothing() -> None:
    world = World()
    world.handle(world.page())
    texts, calls = [c.text for c in world.index.chunks(PAGE)], world.client.calls

    assert world.handle(acl_change("HR-PL-01", "1.2", HR)) == [DiffAction.ACL_ONLY]

    (fast,) = world.fast  # a new page has no fast lane: T8 publishes it after T7
    assert fast.action is T8Action.READERS
    assert fast.replaced == len(texts)
    assert [c.text for c in world.index.chunks(PAGE)] == texts
    assert {c.acl for c in world.index.chunks(PAGE)} == {HR}
    assert world.client.calls == calls
    assert world.sees("alice") == 0
    assert world.sees("bob") == len(texts)
    record = world.permissions.readers(PAGE)
    assert record is not None
    assert record.acl_version == "1.2"


def test_an_edit_that_also_revokes_closes_the_old_chunks_even_if_the_model_is_down() -> None:
    world = World()
    world.handle(world.page())
    assert world.sees("alice") > 0
    world.client.fail_next("unavailable")
    edited = world.page("2.1", BODY.replace("5 unused", "7 unused"), acl=HR)

    with pytest.raises(NoModelAvailableError):
        world.t2.handle(edited)

    assert world.sees("alice") == 0  # the revocation did not wait for T7
    assert {c.document_version for c in world.index.chunks(PAGE)} == {"1.1"}  # old text
    assert world.handle(edited) == [DiffAction.PROCESS]  # the model is back
    assert {c.document_version for c in world.index.chunks(PAGE)} == {"2.1"}
    assert world.sees("alice") == 0
    assert world.sees("bob") > 0


@pytest.mark.parametrize(
    "expanded", [Acl(users=frozenset({"carla"})), Acl(groups=frozenset({"es-employees"}))]
)
def test_edit_with_new_readers_withholds_old_text_through_retry(expanded: Acl) -> None:
    world = World()
    world.handle(world.page())
    world.client.fail_next("unavailable")
    edited = world.page("2.1", BODY.replace("5 unused", "7 unused"), acl=expanded)

    with pytest.raises(NoModelAvailableError):
        world.t2.handle(edited)
    assert world.index.chunks(PAGE) == ()
    assert world.sees("carla") == world.sees("bob") == 0

    world.handle(edited)
    assert world.sees("carla") > 0
    assert {c.document_version for c in world.index.chunks(PAGE)} == {"2.1"}


def test_acl_notification_for_changed_text_withholds_old_text_on_a_grant() -> None:
    world = World()
    world.handle(world.page(acl=HR))
    event = acl_change("HR-PL-01", "2", IT_HR, content_hash="sha256:changed")
    assert world.handle(event) == [DiffAction.ACL_ONLY]
    assert world.index.chunks(PAGE) == ()


def test_accepted_delete_closes_search_until_a_new_lifetime_is_published() -> None:
    world = World()
    world.handle(world.page())
    removal = world.changelog.publish(delete("HR-PL-01", "2")).event
    assert world.sees("bob") == 0
    assert PAGE not in world.index.documents()
    world.handle(removal)
    recreated = world.changelog.publish(world.page()).event
    world.handle(recreated)
    assert world.sees("bob") > 0
    assert {c.original_lifetime for c in world.index.chunks(PAGE)} == {1}


def test_accepted_identity_delete_closes_membership_before_purge() -> None:
    world = World()
    world.handle(world.page())
    removal = world.changelog.publish(delete("users/alice", "2", source="identity")).event
    assert world.permissions.principals("alice") is None
    assert "alice" not in {m.user_id for m in world.permissions.memberships()}
    assert world.sees("alice") == 0
    world.handle(removal)
    recreated = world.changelog.publish(world.user("alice", *USERS["alice"])).event
    world.handle(recreated)
    assert world.sees("alice") > 0


def test_a_purge_removes_the_document_from_search_and_its_readers_record() -> None:
    """The deletion gate: a deleted page is never returned, to anyone."""
    world = World()
    world.handle(world.page())
    assert world.handle(delete("HR-PL-01", "2")) == [DiffAction.PURGE]
    assert world.index.chunks() == ()
    assert world.sees("bob") == 0
    assert world.permissions.readers(PAGE) is None


def test_a_purged_identity_record_takes_its_membership_with_it() -> None:
    world = World()
    world.handle(world.page())
    world.handle(delete("users/alice", "9", source="identity"))
    assert world.permissions.membership("alice") is None
    assert world.sees("alice") == 0


def test_the_wiring_names_the_options() -> None:
    assert resolver().resolver_id == "source_acl@1"
    with pytest.raises(UnknownOptionError):
        search_store("opensearch")  # S17
    with pytest.raises(UnknownOptionError):
        permission_store("dynamodb")  # S17
