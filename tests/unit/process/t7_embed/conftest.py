"""Builders for T7 tests: chunks as T4 and T5 leave them, ready to embed."""

from __future__ import annotations

from ekb.contracts import Chunk, ParsedDocument
from ekb.process.t5_classify.options.rules import RulesClassifier
from ekb.process.t5_classify.step import ClassifyStep
from tests.unit.process.t4_chunk.conftest import page, parsed
from tests.unit.process.t5_classify.conftest import cut


def labelled(document: ParsedDocument) -> tuple[Chunk, ...]:
    """``document`` cut (T4) and labelled (T5): what T7 is given."""
    return ClassifyStep(RulesClassifier()).handle(document, cut(document)).chunks


def synthetic(page_id: str) -> tuple[ParsedDocument, tuple[Chunk, ...]]:
    document = page(page_id)
    return document, labelled(document)


def chunks_of(
    body: str, *, key: str = "hr_docs:X", title: str = "Policy"
) -> tuple[ParsedDocument, tuple[Chunk, ...]]:
    document = parsed(body, key=key, title=title)
    return document, labelled(document)
