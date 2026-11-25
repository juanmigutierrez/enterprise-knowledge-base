"""Port for T6 · Entities. Every linking option implements :class:`EntityLinker`.

A linker reads one document (T3's :class:`~ekb.contracts.ParsedDocument`), its
chunks (T4, labelled by T5) and the entities known right now, and returns, per
chunk, the **mentions** it found: which entity, the words that named it, and
the rule that matched.

Like T5's classifier, it returns mentions, not chunks. The step
(:class:`~ekb.process.t6_entities.step.LinkStep`) writes the IDs onto the
chunks and refuses an ID the directory does not hold, so a linker can neither
change a chunk nor invent an entity.

**The words someone wrote, not what a model wrote about them.** A linker reads
the chunk's text and its section headings, never its context header: the
header is a model's summary (S8), and a link must point to words in the
source. **Never the ACL either**, as in T5: a permission change takes T2's fast
lane, which does not run T6.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ekb.contracts import Chunk, Entity, EntityMention, ParsedDocument


def words(chunk: Chunk) -> tuple[str, ...]:
    """What a linker reads of a chunk: its section trail, then its text.

    The trail holds the document's title and the headings above the passage,
    which name a subject the passage itself may not repeat.
    """
    # T4 also renders headings inside chunk.text. Blank lines separate
    # rendered blocks, while a single newline can wrap one person's name.
    return (*chunk.section, *chunk.text.split("\n\n"))


@runtime_checkable
class EntityDirectory(Protocol):
    """Read access to the known entities: all a linker's step needs from the store."""

    def entities(self) -> tuple[Entity, ...]: ...


@dataclass(frozen=True)
class Linking:
    """One document's mentions, one tuple per chunk in order, and what found them."""

    mentions: tuple[tuple[EntityMention, ...], ...]
    linker_id: str
    """For ``Chunk.linker_id``: ``exact_match@2``."""
    note: str = ""
    """In plain words: how many entities were known, and any name skipped as ambiguous."""


@runtime_checkable
class EntityLinker(Protocol):
    """Finds the known entities each chunk of one document names."""

    @property
    def linker_id(self) -> str:
        """The option and its version, e.g. ``exact_match@2``."""
        ...

    def link(
        self, document: ParsedDocument, chunks: tuple[Chunk, ...], entities: Sequence[Entity]
    ) -> Linking: ...
