"""T4's context header (S8): the port, the ``none`` and ``model`` options, and the step."""

from __future__ import annotations

import json

import pytest

from ekb.contracts import Chunk, ParsedDocument
from ekb.platform.gateway.errors import NoModelAvailableError
from ekb.process.t4_chunk.header import HeaderOutcome, Headers, clean
from ekb.process.t4_chunk.options import header_model
from ekb.process.t4_chunk.options.header_model import ModelHeaderWriter
from ekb.process.t4_chunk.options.header_none import NoHeader
from ekb.process.t4_chunk.options.structure_aware import StructureAwareChunker
from ekb.process.t4_chunk.step import ChunkError, ChunkStep
from tests.unit.platform.gateway.helpers import Scripted, World, make_config, make_world
from tests.unit.process.t4_chunk.conftest import page, parsed

BODY = "# Remote work\n\n## The rule\nTwo days a week.\n\n## Equipment\nA laptop.\n"


@pytest.fixture
def world() -> World:
    return make_world(
        make_config(tasks={"context_header": ("fake:one", "fake:two"), "classify": ("fake:two",)})
    )


def _cut(document: ParsedDocument) -> tuple[Chunk, ...]:
    return StructureAwareChunker().chunk(document)


def test_none_writes_an_empty_header_for_every_chunk() -> None:
    document = parsed(BODY)
    headers = NoHeader().write(document, _cut(document))
    assert headers == Headers(
        texts=("", ""),
        header_id="none@1",
        outcome=HeaderOutcome.NONE,
        note="no header: the none option writes none",
    )


def test_the_model_writes_every_header_in_one_call_per_document(world: World) -> None:
    document = parsed(BODY, key="hr_docs:HR-PL-04")
    chunks = _cut(document)

    headers = ModelHeaderWriter(world.gateway).write(document, chunks)

    assert headers.texts == ("one chunk_0", "one chunk_1")  # the fake fills the schema in
    assert headers.outcome is HeaderOutcome.WRITTEN
    assert headers.header_id == "model@1(header@1, fake:one)"
    assert headers.note == "written by fake:one, one request"
    (record,) = world.log.records()  # one request for the whole document (D16 #4)
    assert (record.task, record.prompt_version, record.document_key) == (
        "context_header",
        "header@1",
        "hr_docs:HR-PL-04",
    )


def test_an_unchanged_document_s_headers_come_from_the_cache(world: World) -> None:
    document = parsed(BODY)
    writer = ModelHeaderWriter(world.gateway)
    first = writer.write(document, _cut(document))
    again = writer.write(document, _cut(document))
    assert again.texts == first.texts
    assert again.outcome is HeaderOutcome.CACHED
    assert again.note == "written by fake:one, from the cache, no request"
    assert world.clients["fake:one"].calls == 1  # the second time reached no model


def test_an_edited_document_gets_new_headers(world: World) -> None:
    writer = ModelHeaderWriter(world.gateway)
    writer.write(parsed(BODY), _cut(parsed(BODY)))
    edited = parsed(BODY.replace("Two days", "Three days"), version="2.1")
    assert writer.write(edited, _cut(edited)).outcome is HeaderOutcome.WRITTEN


def test_the_reply_is_tagged_so_a_deleted_document_s_headers_can_be_forgotten(
    world: World,
) -> None:
    document = parsed(BODY, key="hr_docs:HR-PL-04")
    ModelHeaderWriter(world.gateway).write(document, _cut(document))
    assert world.cache.size() == 1
    assert world.gateway.forget("hr_docs:HR-PL-04") == 1
    assert world.cache.size() == 0


def test_the_request_holds_the_document_the_passages_and_a_schema_per_chunk() -> None:
    scripted = Scripted(json.dumps({"chunk_0": "a", "chunk_1": "b"}))
    document = parsed(BODY, title="Lavoro da remoto")
    document = document.model_copy(update={"language": "it"})
    ModelHeaderWriter(scripted).write(document, _cut(document))

    (asked,) = scripted.asked
    assert asked["task"] == "context_header"
    assert asked["prompt_version"] == "header@1"
    assert asked["document_key"] == document.document_key
    assert '<document title="Lavoro da remoto">' in asked["prompt"]
    assert "<chunk_1>\nEquipment\n\nA laptop.\n</chunk_1>" in asked["prompt"]
    assert "Write in Italian." in asked["system"]  # the document's language
    assert "chunk_0 to chunk_1" in asked["system"]
    schema = asked["json_schema"]
    assert schema["required"] == ["chunk_0", "chunk_1"]
    assert set(schema["properties"]) == {"chunk_0", "chunk_1"}
    assert "additionalProperties" not in schema  # not every provider accepts it
    assert asked["max_output_tokens"] == 256 + 96 * 2


def test_output_is_capped_however_many_chunks() -> None:
    assert header_model.max_output_tokens(1000) == 4096


@pytest.mark.parametrize(
    ("reply", "texts", "outcome", "note"),
    [
        ('{"chunk_0": "a", "chunk_1": "b"}', ("a", "b"), HeaderOutcome.WRITTEN, ""),
        ('{"chunk_0": "a"}', ("a", ""), HeaderOutcome.WRITTEN, "1 of 2 headers missing"),
        ('{"chunk_0": 7, "chunk_1": "b"}', ("", "b"), HeaderOutcome.WRITTEN, "1 of 2 headers"),
        ('{"chunk_0": "a", "chunk_1": "b", "extra": "x"}', ("a", "b"), HeaderOutcome.WRITTEN, ""),
        ("not json", ("", ""), HeaderOutcome.FAILED, "was not the JSON asked for"),
        ('["a", "b"]', ("", ""), HeaderOutcome.FAILED, "was not the JSON asked for"),
    ],
)
def test_the_reply_is_read_one_header_per_chunk(
    reply: str, texts: tuple[str, ...], outcome: HeaderOutcome, note: str
) -> None:
    document = parsed(BODY)
    headers = ModelHeaderWriter(Scripted(reply)).write(document, _cut(document))
    assert (headers.texts, headers.outcome) == (texts, outcome)
    assert note in headers.note


def test_no_model_available_leaves_every_header_empty_and_says_why() -> None:
    """Never blocks the document: a missing header is an enrichment lost, not a gate."""
    error = NoModelAvailableError("context_header", (), None)
    document = parsed(BODY)
    headers = ModelHeaderWriter(Scripted(error=error)).write(document, _cut(document))
    assert headers.texts == ("", "")
    assert headers.outcome is HeaderOutcome.FAILED
    assert headers.header_id == "model@1(header@1, no header)"
    assert headers.note.startswith("no header: no model could write them (no model available")


def test_a_long_header_is_cut_to_the_word_limit_on_one_line() -> None:
    long = " ".join(["word"] * 80) + "\n\nsecond line"
    document = parsed("# Policy\n\nOne line.\n")
    reply = json.dumps({"chunk_0": long})
    (text,) = ModelHeaderWriter(Scripted(reply), max_words=50).write(document, _cut(document)).texts
    assert text == " ".join(["word"] * 50) + " …"
    assert clean("  a\n b\tc ") == "a b c"


def test_a_document_without_chunks_makes_no_call() -> None:
    scripted = Scripted()
    document = parsed("# Only a title\n")
    assert ModelHeaderWriter(scripted).write(document, ()).texts == ()
    assert scripted.asked == []


# ------------------------------------------------------------------- the step


def test_the_step_puts_the_headers_on_the_chunks_and_nothing_else(world: World) -> None:
    document = page("HR-PL-01")
    plain = ChunkStep(StructureAwareChunker()).handle(document)
    headed = ChunkStep(StructureAwareChunker(), ModelHeaderWriter(world.gateway)).handle(document)

    assert [c.context_header for c in headed.chunks] == [f"one chunk_{i}" for i in range(4)]
    assert {c.header_id for c in headed.chunks} == {"model@1(header@1, fake:one)"}
    for before, after in zip(plain.chunks, headed.chunks, strict=True):
        assert after.model_copy(update={"context_header": "", "header_id": ""}) == before
        assert not after.publishable  # a header opens no gate
    assert headed.headers is not None
    assert headed.reason.endswith("; headers: written by fake:one, one request")
    assert plain.headers is None  # S6's wiring: no header writer, no header
    assert {c.header_id for c in plain.chunks} == {""}


def test_the_step_refuses_a_writer_that_returns_the_wrong_number_of_headers() -> None:
    class Short:
        header_id = "short@1"

        def write(self, document: ParsedDocument, chunks: tuple[Chunk, ...]) -> Headers:
            return Headers(("only one",), self.header_id, HeaderOutcome.WRITTEN)

    with pytest.raises(ChunkError, match="4 chunks but 1 headers from short@1"):
        ChunkStep(StructureAwareChunker(), Short()).handle(page("HR-PL-01"))


def test_the_step_puts_each_header_on_one_line() -> None:
    class Messy:
        header_id = "messy@1"

        def write(self, document: ParsedDocument, chunks: tuple[Chunk, ...]) -> Headers:
            return Headers(("  two\n lines ",) * len(chunks), self.header_id, HeaderOutcome.WRITTEN)

    result = ChunkStep(StructureAwareChunker(), Messy()).handle(parsed(BODY))
    assert {c.context_header for c in result.chunks} == {"two lines"}
