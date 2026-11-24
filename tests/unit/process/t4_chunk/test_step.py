"""Running T4 for one document, and the rules the step holds every chunker to."""

from __future__ import annotations

import pytest

from ekb.contracts import Acl, Chunk, ParsedDocument, Sensitivity
from ekb.process.t4_chunk.options.structure_aware import StructureAwareChunker
from ekb.process.t4_chunk.port import new_chunk
from ekb.process.t4_chunk.step import ChunkError, ChunkStep, summary
from tests.unit.process.t4_chunk.conftest import page, parsed


class Returning:
    """A chunker that returns whatever the test gives it."""

    chunker_id = "test@1"
    max_tokens = 10

    def __init__(self, *chunks: Chunk) -> None:
        self._chunks = chunks

    def chunk(self, document: ParsedDocument) -> tuple[Chunk, ...]:
        return self._chunks


DOC = parsed("# Leave\n\n## Booking\nAsk two weeks ahead.\n")


def _good(position: int = 0, text: str = "Booking\n\nAsk two weeks ahead.") -> Chunk:
    return new_chunk(DOC, position, text, ("Leave", "Booking"), "test@1")


def test_it_chunks_one_document_and_says_how() -> None:
    step = ChunkStep(StructureAwareChunker())
    result = step.handle(page("HR-PL-01"))
    assert len(result.chunks) == 4
    assert result.document.document_key == "hr_docs:HR-PL-01"
    assert (result.chunker_id, result.max_tokens) == (step.chunker_id, 400)
    assert result.reason == (
        "chunked with structure_aware@1(max_tokens=400): 4 chunks, 38-59 tokens"
    )
    assert result.over_limit == 0


def test_nothing_leaves_t4_publishable() -> None:
    """Publication gate, first line: T4 sets no sensitivity, vector or ACL."""
    for chunk in ChunkStep(StructureAwareChunker()).handle(page("HR-CMP-01")).chunks:
        assert not chunk.publishable
        assert (chunk.sensitivity, chunk.vector, chunk.acl, chunk.context_header) == (
            None,
            None,
            None,
            "",
        )


@pytest.mark.parametrize(
    "field",
    [
        {"acl": Acl(groups=frozenset({"all-staff"}))},
        {"sensitivity": Sensitivity.INTERNAL},
        {"vector": (0.1, 0.2)},
    ],
    ids=["acl", "sensitivity", "vector"],
)
def test_a_chunker_that_sets_what_later_steps_own_is_refused(field: dict[str, object]) -> None:
    sneaky = _good().model_copy(update=field)
    with pytest.raises(ChunkError, match="only T5, T7 and T8 set them"):
        ChunkStep(Returning(sneaky)).handle(DOC)


@pytest.mark.parametrize(
    ("chunk", "problem"),
    [
        (_good().model_copy(update={"document_version": "0.9"}), "says it is from"),
        (_good().model_copy(update={"document_key": "hr_docs:other"}), "says it is from"),
        (_good(position=1), "is numbered 1"),
        (_good().model_copy(update={"chunk_id": "x#0"}), r"is numbered 0 \(x#0\)"),
        (_good(text="  \n"), "is empty"),
    ],
    ids=["old version", "other document", "position", "chunk id", "empty"],
)
def test_a_chunker_that_breaks_the_port_rules_fails_loudly(chunk: Chunk, problem: str) -> None:
    with pytest.raises(ChunkError, match=problem):
        ChunkStep(Returning(chunk)).handle(DOC)


def test_chunks_over_the_limit_are_counted() -> None:
    big = _good(text="Booking\n\n" + "word " * 20)
    result = ChunkStep(Returning(big)).handle(DOC)
    assert result.over_limit == 1
    assert result.reason.endswith("1 chunk, 21 tokens, 1 over the 10-token limit")


def test_summaries_say_how_many_and_how_big() -> None:
    small, large = _good(), _good(position=1, text="Booking " * 12)
    assert summary([]) == "0 chunks"
    assert summary([small]) == "1 chunk, 6 tokens"  # Booking Ask two weeks ahead .
    assert summary([small, small]) == "2 chunks, 6 tokens"
    assert summary([small, large], max_tokens=10) == (
        "2 chunks, 6-12 tokens, 1 over the 10-token limit"
    )
