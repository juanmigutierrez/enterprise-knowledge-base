"""Builders for T5 tests: parsed documents and their chunks, as T3 and T4 make them."""

from __future__ import annotations

from ekb.contracts import Chunk, ParsedDocument
from ekb.process.t4_chunk.options.structure_aware import StructureAwareChunker
from tests.unit.process.t4_chunk.conftest import page, parsed


def cut(document: ParsedDocument) -> tuple[Chunk, ...]:
    return StructureAwareChunker().chunk(document)


def document(
    body: str, *, title: str = "Policy", department: str | None = "people", key: str = "hr_docs:X"
) -> ParsedDocument:
    """A parsed document whose source filed it under ``department`` (or under nothing)."""
    return parsed(body, key=key, title=title).model_copy(update={"department": department})


def synthetic(page_id: str) -> ParsedDocument:
    """One of the twelve synthetic pages, parsed, with its source's department."""
    from tests.unit.process.t4_chunk.conftest import PAGES

    (found,) = [p for p in PAGES if p.id == page_id]
    return page(page_id).model_copy(update={"department": found.department})
