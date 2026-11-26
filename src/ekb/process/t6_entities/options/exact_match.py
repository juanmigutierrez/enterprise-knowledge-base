"""T6 option ``exact_match``: known names found word for word, no model.

The first tier of card 4.10's cascade and the ``ci`` profile's choice: the
names come from the HR record API (through the entity store), the rules from
``ekb.process.t6_entities.match``. It finds "Bruno Moretti", "the People team"
and "daniela.ferri@example.com"; it leaves "the Head of People's approval" as
a mention of the team, not of Bruno, and a bare "Engineering" unlinked. Those
are the model tier's job.

The rules are built again for every document from the entities known at that
moment (a few dozen here), so a new hire is matched from the next document
on. At thousands of entities, production keeps one compiled index and
rebuilds it when the store changes.
"""

from __future__ import annotations

from collections.abc import Sequence

from ekb.contracts import Chunk, Entity, ParsedDocument
from ekb.platform.registry import register
from ekb.process.t6_entities import match
from ekb.process.t6_entities.port import Linking, words


@register("t6_entities", "exact_match")
class ExactMatchLinker:
    @property
    def linker_id(self) -> str:
        return f"exact_match@{match.VERSION}"

    def link(
        self, document: ParsedDocument, chunks: tuple[Chunk, ...], entities: Sequence[Entity]
    ) -> Linking:
        index = match.Index(entities)
        found = [match.find_all(index, words(chunk)) for chunk in chunks]
        return Linking(
            mentions=tuple(f.mentions for f in found),
            linker_id=self.linker_id,
            note=_note(index, found),
        )


def _note(index: match.Index, found: Sequence[match.Found]) -> str:
    if not index.people and not index.teams:
        return "no entities known yet: nothing to link to"
    note = f"matched against {index.people} people and {index.teams} teams"
    skipped = sorted({name for f in found for name in f.ambiguous})
    if skipped:
        note += f"; not linked, ambiguous: {'; '.join(skipped)}"
    return note
