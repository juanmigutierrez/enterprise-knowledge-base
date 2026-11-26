"""Which published pages an entity change affects, and whether their links are stale (S16).

T6 writes a page's links when the page is processed. People and teams change
on their own schedule: a hire, a leaver, a renamed team. Without this module,
a page keeps the links it was given until someone edits it (S9's limit)::

    entity change (before, after) ─▶ candidates(): pages that carry its ID, or
                                     whose words name it, before or after
                                  ─▶ stale(): would T6 link them differently now?
                                  ─▶ the worker re-processes those (ekb.app.pipeline)

**Both directions, both versions.** A leaver's ID is on pages that named them
(the ``before``); a hire is named by pages that could not link to them yet (the
``after``); a renamed team is both. And a hire who shares a name with someone
already linked makes that name ambiguous, so the page must lose the old link:
the page names the new entity's name, so it is a candidate too.

**The same reading as T6.** The words are read with :func:`~ekb.process.t6_entities.port.words`
and matched with :func:`~ekb.process.t6_entities.match.find_all`, exactly as
``exact_match`` reads them, so "stale" means "``exact_match`` would write other
links today". Pages linked by another linker (a model tier, later) are always
re-processed when they are candidates: their links can't be predicted here.

It reads published chunks only, so a page the search store does not hold (not
published yet, parked in the dead letters, deleted) is never a candidate: it is
linked against the current entities when it is next processed anyway.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ekb.contracts import Chunk, Entity
from ekb.process.t6_entities import match
from ekb.process.t6_entities.port import words

EXACT_MATCH = f"exact_match@{match.VERSION}"
"""The linker ID whose links :func:`stale` can predict."""


@dataclass(frozen=True)
class EntityChange:
    """One change to the entity store: what the record defined before, and now."""

    before: Entity | None
    after: Entity | None

    @property
    def entity_id(self) -> str:
        entity = self.after or self.before
        return entity.entity_id if entity is not None else ""

    @property
    def matters(self) -> bool:
        """Whether a page's links could change: an entity came or went, or its ID, name or
        aliases changed. A new office or manager changes no link."""
        return _forms(self.before) != _forms(self.after)

    def describe(self) -> str:
        """``person:sara added``, ``person:lucia removed``, ``team:T-PPL renamed``: IDs
        only, because a trace outlives a leaver (no names, S12)."""
        if self.before is None:
            return f"{self.entity_id} added"
        if self.after is None:
            return f"{self.entity_id} removed"
        if self.before.entity_id != self.after.entity_id:
            return f"{self.before.entity_id} is now {self.after.entity_id}"
        return f"{self.entity_id} renamed"


def _forms(entity: Entity | None) -> tuple[str, str, str, tuple[str, ...]] | None:
    if entity is None:
        return None
    return (entity.entity_id, entity.kind.value, entity.name, tuple(sorted(entity.aliases)))


def candidates(chunks: Iterable[Chunk], changes: Sequence[EntityChange]) -> dict[str, list[str]]:
    """The pages each change may affect: ``document_key -> [what changed, …]``.

    A page is a candidate if one of its chunks carries a changed entity's ID, or
    names the entity (before or after the change) by T6's rules.
    """
    relevant = [change for change in changes if change.matters]
    checks: list[tuple[EntityChange, set[str], list[match.Index]]] = []
    for change in relevant:
        ids = {e.entity_id for e in (change.before, change.after) if e is not None}
        indexes = [match.Index([e]) for e in (change.before, change.after) if e is not None]
        checks.append((change, ids, indexes))
    found: dict[str, list[str]] = {}
    for chunk in chunks:
        for change, ids, indexes in checks:
            why = change.describe()
            if why in found.get(chunk.document_key, ()):
                continue
            if ids & set(chunk.entity_ids) or any(
                match.find_all(index, words(chunk)).mentions for index in indexes
            ):
                found.setdefault(chunk.document_key, []).append(why)
    return found


def stale(chunks: Sequence[Chunk], entities: Sequence[Entity]) -> bool:
    """Whether one page's published chunks carry other links than T6 would write today.

    Always true for chunks another linker (or another version of the rules) wrote,
    because their links can't be predicted here.
    """
    index = match.Index(entities)
    for chunk in chunks:
        if chunk.linker_id != EXACT_MATCH:
            return True
        found = match.find_all(index, words(chunk))
        if tuple(sorted({m.entity_id for m in found.mentions})) != tuple(sorted(chunk.entity_ids)):
            return True
    return False
