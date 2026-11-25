"""Entity contract: the people and teams that T6 links chunks to (S9).

An **entity** is something the company knows under an ID of its own: a person
or a team today, later a retailer, a brand or a campaign (D6). The entity store
keeps them; T6 writes their IDs onto the chunks that mention them
(``Chunk.entity_ids``), so a search can later ask for "everything about the
People team" without matching words.

**IDs are canonical, never a source's own.** A person's ID is the identity
provider's user ID (D3): ``person:bob``, whatever the HR system calls him
(``E-1002``). The ACLs use the same user IDs, so "documents about Bruno" and
"documents Bruno may read" talk about one person. Teams exist only in the HR
system, so a team's ID is the HR team ID: ``team:T-PPL``.

**Every entity knows the record that defines it** (``source_key``, the record's
``document_key``). When that record is deleted, T2's purge removes the entity:
a leaver is no longer someone to link to.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator


class EntityKind(StrEnum):
    """What an entity is. The kind is also the prefix of its ID."""

    PERSON = "person"
    TEAM = "team"


def entity_id(kind: EntityKind, local_id: str) -> str:
    """``person:bob``, ``team:T-PPL``: the kind, then the canonical ID within it."""
    return f"{kind.value}:{local_id}"


class Entity(BaseModel):
    """One canonical person or team, as the entity store holds it."""

    model_config = ConfigDict(frozen=True)

    entity_id: str
    kind: EntityKind
    name: str
    """As the record system writes it: ``Lucía Navarro``, ``People``."""
    aliases: tuple[str, ...] = ()
    """Other exact forms that name it, e.g. a person's work email."""
    team_id: str | None = None
    """A person's team, as an entity ID (``team:T-PPL``). ``None`` for a team."""
    source_key: str
    """The record that defines it, e.g. ``hr_api:employees/E-1002``. Purging that
    record removes the entity."""
    source_version: str
    """The record's version the entity was read from."""

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        prefix = f"{self.kind.value}:"
        if not self.entity_id.startswith(prefix) or self.entity_id == prefix:
            raise ValueError(f"a {self.kind.value}'s ID is {prefix}<id>, not {self.entity_id!r}")
        if not self.name.strip():
            raise ValueError("an entity must have a name")
        if self.team_id is not None and (
            self.kind is not EntityKind.PERSON
            or not self.team_id.startswith("team:")
            or self.team_id == "team:"
        ):
            raise ValueError("only a person has a team_id, and it is a team's entity ID")
        return self


class EntityMention(BaseModel):
    """Where one chunk names one entity: the evidence behind a link.

    T6 returns mentions with its result (the demo and the inspector show them);
    only the entity IDs go onto the chunk.
    """

    model_config = ConfigDict(frozen=True)

    entity_id: str
    text: str
    """The words as the chunk writes them: ``Lucia Navarro``, ``People team``."""
    rule: str
    """Which rule found it: ``name``, ``email`` or ``team`` for ``exact_match``; a
    model tier will add its own."""
