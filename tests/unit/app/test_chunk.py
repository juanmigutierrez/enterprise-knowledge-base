"""T4 behind T3 and T2 (until S12): chunk what T3 parsed, forget what T2 purges."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ekb.app.changelog import ChangeLog
from ekb.app.chunk import Processed, chunk_after_t3, chunk_step, chunker
from ekb.app.diff import diff_step
from ekb.app.inspector import _create_app
from ekb.app.parse import parse_after_t2, parse_step
from ekb.contracts import Acl, Chunk, DiffAction, ParsedDocument
from ekb.operate.inspector.log import ChunkStore, DecisionLog, EventLog, ParsedStore
from ekb.platform.registry import UnknownOptionError
from ekb.process.t2_diff.step import DiffStep
from ekb.process.t3_parse.port import ParseError
from ekb.process.t4_chunk.step import ChunkStep
from tests.unit.changelog.factories import acl_change, delete, upsert
from tests.unit.process.t3_parse.conftest import page_json

KEY = "hr_docs:HR-PL-04"
BODY = "# Remote work\n\n## The rule\nTwo days a week.\n\n## Equipment\nA laptop.\n"


class Kept:
    def __init__(self) -> None:
        self.processed: list[Processed] = []
        self.forgotten: list[str] = []


class Broken:
    """A chunker that always fails, as a bug would."""

    chunker_id = "broken@1"
    max_tokens = 400

    def chunk(self, document: ParsedDocument) -> tuple[Chunk, ...]:
        raise RuntimeError("chunker bug")


@pytest.fixture
def changelog() -> ChangeLog:
    changelog = ChangeLog.in_memory()
    changelog.store_original(KEY, "1.1", page_json(BODY))
    return changelog


def _pipeline(changelog: ChangeLog, kept: Kept, t4: ChunkStep | None = None) -> DiffStep:
    then = chunk_after_t3(t4 or chunk_step(), kept.processed.append)
    return diff_step(changelog, parse_after_t2(parse_step(changelog), then, kept.forgotten.append))


def test_only_process_decisions_are_chunked_and_purges_are_forgotten(changelog: ChangeLog) -> None:
    kept = Kept()
    t2 = _pipeline(changelog, kept)
    first = upsert("HR-PL-04", "1.1")
    actions = [
        t2.handle(event).decision.action
        for event in (
            first,  # new: process
            first,  # a repeat: skip
            acl_change("HR-PL-04", "1.2", Acl(groups=frozenset({"it-employees"}))),  # acl_only
            delete("HR-PL-04", "1.2"),  # purge
        )
    ]
    assert actions == [DiffAction.PROCESS, DiffAction.SKIP, DiffAction.ACL_ONLY, DiffAction.PURGE]
    (result,) = kept.processed  # T3 and T4 ran once: never for a skip or the fast lane
    assert result.chunks is not None
    assert [c.text for c in result.chunks.chunks] == [
        "The rule\n\nTwo days a week.",
        "Equipment\n\nA laptop.",
    ]
    assert result.chunks.document is result.parse.parsed  # T4 chunked what T3 parsed
    assert kept.forgotten == [KEY]  # the purge reaches whoever keeps the chunks


def test_a_record_is_parsed_as_not_a_document_and_never_chunked(changelog: ChangeLog) -> None:
    kept = Kept()
    _pipeline(changelog, kept).handle(upsert("users/alice", "1", source="identity"))
    (result,) = kept.processed
    assert (result.parse.parsed, result.chunks) == (None, None)


def test_if_t4_fails_nothing_is_kept_and_t2_records_nothing(changelog: ChangeLog) -> None:
    """S4's rule holds through T4: a failure is never mistaken for done."""
    kept = Kept()
    t2 = _pipeline(changelog, kept, ChunkStep(Broken()))
    for _ in range(2):  # the bus delivers it again: decided as new again, fails again
        with pytest.raises(RuntimeError, match="chunker bug"):
            t2.handle(upsert("HR-PL-04", "1.1"))
        assert changelog.registry.get(KEY) is None
    assert kept.processed == []  # not even T3's result: nothing half-recorded


def test_the_wiring_names_the_chunkers_and_passes_their_settings() -> None:
    assert chunker().chunker_id == "structure_aware@1(max_tokens=400)"
    assert chunker("fixed", max_tokens=60, overlap_tokens=10).chunker_id == (
        "fixed@1(max_tokens=60,overlap_tokens=10)"
    )
    assert chunk_step(max_tokens=120).max_tokens == 120
    with pytest.raises(UnknownOptionError):
        chunker("semantic")  # a later option of card 4.2, not built yet
    with pytest.raises(TypeError):
        chunker(context_header="model")  # type: ignore[arg-type]  # S8's port, not the chunker's


def test_published_delete_hides_chunks_while_t2_is_blocked(changelog: ChangeLog) -> None:
    """Deletion gate, inspector side: chunks of a deleted page are never returned."""
    parsed, chunks = ParsedStore(), ChunkStore()

    def keep(result: Processed) -> None:
        assert result.parse.parsed is not None
        assert result.chunks is not None
        parsed.record(result.parse.event, result.parse.parsed)
        chunks.record(
            result.parse.event,
            result.parse.parsed.title,
            result.chunks.chunks,
            chunker_id=result.chunks.chunker_id,
            max_tokens=result.chunks.max_tokens,
        )

    def forget(key: str) -> None:
        parsed.drop(key)
        chunks.drop(key)

    then = chunk_after_t3(chunk_step(), keep)
    step = diff_step(changelog, parse_after_t2(parse_step(changelog), then, forget))
    app = _create_app(EventLog(), DecisionLog(), parsed, changelog, "local", chunks=chunks)
    client = TestClient(app)
    original = changelog.publish(upsert("HR-PL-04")).event
    step.handle(original)
    url, query = "/api/t4/chunks/document", {"document_key": KEY}
    assert len(client.get(url, params=query).json()["chunks"]) == 2

    changelog.store_original(KEY, "2.1", b"{}")
    broken = changelog.publish(upsert("HR-PL-04", "2.1")).event
    with pytest.raises(ParseError):
        step.handle(broken)  # T2 is stuck on this event: the purge can't run yet
    deleted = changelog.publish(delete("HR-PL-04", "2.1")).event
    assert client.get(url, params=query).status_code == 404  # hidden at once
    assert client.get("/api/t4/chunks").json() == {"documents": [], "chunks": 0}

    pointer = changelog.store_original(KEY, "1.1", page_json("# New\n\nReplacement policy"))
    recreated = changelog.publish(upsert("HR-PL-04", raw_pointer=pointer)).event
    assert client.get(url, params=query).status_code == 404  # not chunked yet
    step.handle(deleted)
    step.handle(recreated)
    (chunk,) = client.get(url, params=query).json()["chunks"]
    assert chunk["text"] == "New\n\nReplacement policy"
