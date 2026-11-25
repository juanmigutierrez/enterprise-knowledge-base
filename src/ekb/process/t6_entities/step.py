"""Running T6 for one document's chunks, and checking what the linker returned.

::

    chunks   = T5's result                                    # cut, headed, labelled
    entities = directory.entities()                           # known right now
    result   = linker.link(parsed, chunks, entities)          # the option: exact_match
    chunks   = linked(parsed, chunks, result, entities)       # entity IDs on each chunk

T6 runs right after T5, on the same document, so only where T2 decided
``process``. A permission change (the fast lane) or an unchanged save (a
skip) never links again: the links come from the text, which didn't change.

**The step checks, the linker only answers.** Every chunk gets exactly one
tuple of mentions, in order; every mentioned ID must be one the directory
holds, so a linker cannot invent an entity or link to one just removed; the
IDs go onto the chunk sorted, each once. And, as in T5, chunks that already
carry an ACL or a vector are refused: T6 runs before T7 and T8.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from ekb.contracts import Chunk, Entity, EntityMention, ParsedDocument
from ekb.process.t6_entities.port import EntityDirectory, EntityLinker, Linking


@dataclass(frozen=True)
class LinkResult:
    document: ParsedDocument
    chunks: tuple[Chunk, ...]
    """The chunks with their ``entity_ids`` and ``linker_id``."""
    linker_id: str
    mentions: tuple[tuple[EntityMention, ...], ...]
    """Per chunk, in order: what named each entity (the evidence for the demo and the UI)."""
    note: str
    reason: str


def summary(chunks: Sequence[Chunk]) -> str:
    """``2 of 4 chunks linked: team:T-PPL x2``: how many chunks name an entity, and which."""
    counts = Counter(entity for chunk in chunks for entity in chunk.entity_ids)
    linked = sum(1 for chunk in chunks if chunk.entity_ids)
    if not counts:
        return f"0 of {len(chunks)} chunks linked"
    which = ", ".join(f"{entity} x{n}" if n > 1 else entity for entity, n in sorted(counts.items()))
    return f"{linked} of {len(chunks)} chunks linked: {which}"


class LinkStep:
    """T6 with one linker, reading the entities from one directory."""

    def __init__(self, linker: EntityLinker, directory: EntityDirectory) -> None:
        self._linker = linker
        self._directory = directory

    @property
    def linker_id(self) -> str:
        return self._linker.linker_id

    def handle(self, document: ParsedDocument, chunks: tuple[Chunk, ...]) -> LinkResult:
        check_input(document, chunks)
        entities = self._directory.entities()
        result = self._linker.link(document, chunks, entities)
        out = linked(document, chunks, result, entities)
        return LinkResult(
            document=document,
            chunks=out,
            linker_id=result.linker_id,
            mentions=result.mentions,
            note=result.note,
            reason=f"linked with {result.linker_id}: {summary(out)}; {result.note}",
        )


class LinkError(ValueError):
    """T6 was given chunks it must not link, or a linker broke its promise."""


def check_input(document: ParsedDocument, chunks: Sequence[Chunk]) -> None:
    for chunk in chunks:
        where = f"{document.document_key} v{document.version}, {chunk.chunk_id}"
        if (chunk.document_key, chunk.document_version) != (
            document.document_key,
            document.version,
        ):
            raise LinkError(f"{where} is from {chunk.document_key} v{chunk.document_version}")
        if chunk.acl is not None or chunk.vector is not None:
            raise LinkError(f"{where} already has an ACL or a vector: T6 runs before T7 and T8")


def linked(
    document: ParsedDocument,
    chunks: tuple[Chunk, ...],
    result: Linking,
    entities: Sequence[Entity],
) -> tuple[Chunk, ...]:
    """``chunks`` with ``result``'s entity IDs on them, after checking every ID is known."""
    if len(result.mentions) != len(chunks):
        raise LinkError(
            f"{document.document_key} v{document.version}: {len(chunks)} chunks but "
            f"{len(result.mentions)} sets of mentions from {result.linker_id}"
        )
    known = {entity.entity_id for entity in entities}
    out: list[Chunk] = []
    for chunk, mentions in zip(chunks, result.mentions, strict=True):
        ids = {mention.entity_id for mention in mentions}
        unknown = sorted(ids - known)
        if unknown:
            raise LinkError(
                f"{chunk.chunk_id}: {result.linker_id} linked to unknown entities {unknown}"
            )
        out.append(
            chunk.model_copy(
                update={"entity_ids": tuple(sorted(ids)), "linker_id": result.linker_id}
            )
        )
    return tuple(out)
