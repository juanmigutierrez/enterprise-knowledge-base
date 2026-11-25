"""Builders for T6 tests: entities as the HR records define them, and chunks to link."""

from __future__ import annotations

import yaml

from ekb.contracts import Chunk, Entity, EntityKind, ParsedDocument, entity_id
from tests.conftest import REPO_ROOT
from tests.unit.process.t4_chunk.conftest import page, parsed
from tests.unit.process.t5_classify.conftest import cut

DATA = REPO_ROOT / "sources" / "synthetic_data"


def person(user: str, name: str, *, email: str | None = None, team: str | None = None) -> Entity:
    return Entity(
        entity_id=entity_id(EntityKind.PERSON, user),
        kind=EntityKind.PERSON,
        name=name,
        aliases=(email,) if email else (),
        team_id=entity_id(EntityKind.TEAM, team) if team else None,
        source_key=f"hr_api:employees/{user}",
        source_version="1",
    )


def team(team_id: str, name: str) -> Entity:
    return Entity(
        entity_id=entity_id(EntityKind.TEAM, team_id),
        kind=EntityKind.TEAM,
        name=name,
        source_key=f"hr_api:teams/{team_id}",
        source_version="1",
    )


def synthetic_entities() -> tuple[Entity, ...]:
    """The 12 people and 5 teams of the synthetic HR records, with identity user IDs.

    Built from the data files the mocks serve, joined by full name as the
    connectors join them (D3).
    """
    hr = yaml.safe_load((DATA / "hr_api.yaml").read_text(encoding="utf-8"))
    directory = yaml.safe_load((DATA / "directory.yaml").read_text(encoding="utf-8"))
    user_of = {user["name"]: uid for uid, user in directory["users"].items()}
    people = [
        person(user_of[e["name"]], e["name"], email=e["work_email"], team=e["team_id"])
        for e in hr["employees"]
    ]
    teams = [team(t["id"], t["name"]) for t in hr["teams"]]
    return (*people, *teams)


ENTITIES = synthetic_entities()


def chunks_of(
    body: str, *, key: str = "hr_docs:X", title: str = "Policy"
) -> tuple[ParsedDocument, tuple[Chunk, ...]]:
    document = parsed(body, key=key, title=title)
    return document, cut(document)


def synthetic(page_id: str) -> tuple[ParsedDocument, tuple[Chunk, ...]]:
    document = page(page_id)
    return document, cut(document)
