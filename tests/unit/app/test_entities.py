"""T6 and the entity store behind T5, T3 and T2 (S9): what runs when, and what a purge removes."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

import pytest

from ekb.app.changelog import ChangeLog
from ekb.app.chunk import Processed, chunk_after_t3, chunk_step
from ekb.app.classify import classify_step, forgetter
from ekb.app.diff import diff_step
from ekb.app.entities import entity_store, entity_sync, link_step, linker
from ekb.app.parse import parse_after_t2, parse_step
from ekb.contracts import Acl, ChangeEvent, Chunk, DiffAction, Entity, ParsedDocument
from ekb.platform.registry import UnknownOptionError
from ekb.process.t2_diff.step import DiffStep
from ekb.process.t6_entities.port import Linking
from ekb.process.t6_entities.step import LinkStep
from ekb.stores.entities.port import EntityStore
from tests.unit.changelog.factories import acl_change, delete, upsert
from tests.unit.process.t3_parse.conftest import page_json

PAGE = "hr_docs:HR-PL-04"
BODY = (
    "# Remote work\n\n## The rule\nTwo days a week.\n\n## Abroad\nTell Lucía Navarro "
    "and the People team.\n"
)
LUCIA = {
    "id": "E-1009",
    "name": "Lucía Navarro",
    "work_email": "lucia.navarro@example.com",
    "team_id": "T-PPL",
    "updated_at": "2026-11-24T09:00:00+00:00",
}
PEOPLE = {"id": "T-PPL", "name": "People", "updated_at": "2026-11-24T09:00:00+00:00"}


class World:
    def __init__(self, t6: LinkStep | None = None) -> None:
        self.changelog = ChangeLog.in_memory()
        self.store: EntityStore = entity_store()
        self.processed: list[Processed] = []
        self.dropped: list[str] = []
        records = entity_sync(self.changelog, self.store)
        then = chunk_after_t3(
            chunk_step(),
            self.processed.append,
            classify_step(),
            t6 or link_step(self.store),
            records,
        )
        forget = forgetter(None, self.dropped.append, records.forget)
        self.t2: DiffStep = diff_step(
            self.changelog, parse_after_t2(parse_step(self.changelog), then, forget)
        )

    def record(self, collection: str, data: Mapping[str, object], **extra: object) -> ChangeEvent:
        source_id = f"{collection}/{data['id']}"
        original = json.dumps(data, sort_keys=True).encode()
        version = str(data["updated_at"])
        pointer = self.changelog.store_original(f"hr_api:{source_id}", version, original)
        return upsert(source_id, version, "hr_api", raw_pointer=pointer, **extra)

    def page(self, version: str = "1.1", body: str = BODY) -> ChangeEvent:
        pointer = self.changelog.store_original(PAGE, version, page_json(body))
        return upsert("HR-PL-04", version, raw_pointer=pointer)

    def handle(self, *events: ChangeEvent) -> list[DiffAction]:
        return [self.t2.handle(event).decision.action for event in events]


def test_hr_records_fill_the_store_and_the_page_is_linked_to_them() -> None:
    world = World()
    assert (
        world.handle(
            world.record("employees", LUCIA, person_id="lucia"),
            world.record("teams", PEOPLE),
            world.page(),
        )
        == [DiffAction.PROCESS] * 3
    )
    lucia, people, page = world.processed
    assert lucia.chunks is None
    assert lucia.record is not None
    assert lucia.record.reason == "person:lucia added: Lucía Navarro"
    assert people.record is not None
    assert people.record.entity is not None
    assert [e.entity_id for e in world.store.entities()] == ["person:lucia", "team:T-PPL"]
    assert [c.entity_ids for c in page.final] == [(), ("person:lucia", "team:T-PPL")]
    assert page.links is not None
    assert page.final == page.links.chunks
    assert {c.sensitivity is not None for c in page.final} == {True}  # T5 ran before T6
    assert not any(c.publishable for c in page.final)


def test_a_skip_or_the_fast_lane_never_links_again() -> None:
    world = World()
    world.handle(world.record("teams", PEOPLE))
    first = world.page()
    revoked = acl_change("HR-PL-04", "1.2", Acl(groups=frozenset({"es-employees"})))
    assert world.handle(first, first, revoked) == [
        DiffAction.PROCESS,
        DiffAction.SKIP,
        DiffAction.ACL_ONLY,
    ]
    assert len(world.processed) == 2  # the team record, the page once


def test_a_purged_hr_record_removes_its_entity_and_the_next_version_is_not_linked_to_it() -> None:
    world = World()
    world.handle(world.record("employees", LUCIA, person_id="lucia"), world.page())
    assert world.handle(delete("employees/E-1009", "2", source="hr_api")) == [DiffAction.PURGE]
    assert world.store.get("person:lucia") is None
    assert world.dropped == ["hr_api:employees/E-1009"]
    # The page processed before the leaver keeps its link until it changes (S9's limit) ...
    assert world.processed[1].final[1].entity_ids == ("person:lucia",)
    # ... and its next version no longer links to someone the store does not hold.
    world.handle(world.page("1.2", BODY + "\n## More\nAgain.\n"))
    assert [c.entity_ids for c in world.processed[-1].final] == [(), (), ()]


def test_a_purged_page_drops_its_chunks_and_leaves_the_entities_alone() -> None:
    world = World()
    world.handle(world.record("teams", PEOPLE), world.page())
    assert world.handle(delete("HR-PL-04", "1.2")) == [DiffAction.PURGE]
    assert world.dropped == [PAGE]
    assert [e.entity_id for e in world.store.entities()] == ["team:T-PPL"]


def test_an_employee_with_no_identity_user_is_no_one_to_link_to() -> None:
    world = World()
    world.handle(world.record("employees", LUCIA, person_id=None), world.page())
    record = world.processed[0].record
    assert record is not None
    assert record.entity is None
    assert "fail closed" in record.reason
    assert world.store.entities() == ()
    assert [c.entity_ids for c in world.processed[1].final] == [(), ()]


def test_if_t6_fails_nothing_is_kept_and_t2_records_nothing() -> None:
    class Broken:
        linker_id = "broken@1"

        def link(
            self, document: ParsedDocument, chunks: tuple[Chunk, ...], entities: Sequence[Entity]
        ) -> Linking:
            raise RuntimeError("linker bug")

    world = World(LinkStep(Broken(), entity_store()))
    for _ in range(2):  # delivered again: decided as new again, fails again
        with pytest.raises(RuntimeError, match="linker bug"):
            world.t2.handle(world.page())
        assert world.changelog.registry.get(PAGE) is None
    assert world.processed == []


def test_the_wiring_names_the_options() -> None:
    assert linker().linker_id == "exact_match@2"
    assert entity_store().entities() == ()
    with pytest.raises(UnknownOptionError):
        linker("cascade")  # card 4.10's starting choice, built later
    with pytest.raises(UnknownOptionError):
        entity_store("dynamodb")  # S17
