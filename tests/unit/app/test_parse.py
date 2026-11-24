"""T3 behind T2 (until S12): parse what T2 sends on, forget what it purges."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ekb.app.changelog import ChangeLog
from ekb.app.diff import diff_step
from ekb.app.inspector import _create_app
from ekb.app.parse import DOCUMENT_SOURCES, parse_after_t2, parse_step, parser
from ekb.contracts import Acl, DiffAction
from ekb.operate.inspector.log import ChunkStore, DecisionLog, EventLog, ParsedStore
from ekb.platform.registry import UnknownOptionError
from ekb.process.t2_diff.step import DiffStep
from ekb.process.t3_parse.port import MARKDOWN, ParseError, SourceFile
from ekb.process.t3_parse.step import ParseResult
from tests.unit.changelog.factories import acl_change, delete, upsert
from tests.unit.process.t3_parse.conftest import page_json

KEY = "hr_docs:HR-PL-04"


class Kept:
    def __init__(self) -> None:
        self.parsed: list[ParseResult] = []
        self.forgotten: list[str] = []


@pytest.fixture
def changelog() -> ChangeLog:
    changelog = ChangeLog.in_memory()
    changelog.store_original(KEY, "1.1", page_json("# Remote work\n\nTwo days.\n"))
    return changelog


def _t2_then_t3(changelog: ChangeLog, kept: Kept) -> DiffStep:
    t3 = parse_step(changelog)
    return diff_step(changelog, parse_after_t2(t3, kept.parsed.append, kept.forgotten.append))


def test_only_process_decisions_are_parsed_and_purges_are_forgotten(changelog: ChangeLog) -> None:
    kept = Kept()
    t2 = _t2_then_t3(changelog, kept)
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
    (result,) = kept.parsed  # T3 ran once: never for a skip or the fast lane
    assert result.parsed is not None
    assert result.parsed.version == "1.1"
    assert kept.forgotten == [KEY]  # the deleted page's parsed text goes too


def test_a_record_reaches_t3_and_comes_back_as_not_a_document(changelog: ChangeLog) -> None:
    kept = Kept()
    _t2_then_t3(changelog, kept).handle(upsert("users/alice", "1", source="identity"))
    (result,) = kept.parsed
    assert result.parsed is None
    assert "not a document" in result.reason


def test_if_t3_fails_t2_records_nothing_so_a_redelivery_is_never_skipped(
    changelog: ChangeLog,
) -> None:
    """S4's rule holds through T3: a failure is never mistaken for done."""
    broken = upsert("HR-PL-07", "1.1")
    changelog.store_original("hr_docs:HR-PL-07", "1.1", b"not a page")
    t2 = _t2_then_t3(changelog, Kept())
    for _ in range(2):  # the bus delivers it again: decided as new again, fails again
        with pytest.raises(ParseError):
            t2.handle(broken)
        assert changelog.registry.get("hr_docs:HR-PL-07") is None


def test_the_wiring_names_the_document_sources_and_the_parsers() -> None:
    assert list(DOCUMENT_SOURCES) == ["hr_docs"]
    assert parser().parser_id.startswith("markdown-it-py@")
    assert parser("plain_text").parser_id == "plain_text@1"
    with pytest.raises(UnknownOptionError):
        parser("docling")  # named in config/default.yaml, not built yet


def test_source_file_validates_input_at_the_parser_boundary() -> None:
    with pytest.raises(ValidationError):
        SourceFile(KEY, "1.1", "Policy", "en", MARKDOWN, None)  # type: ignore[arg-type]


def test_published_delete_hides_parsed_text_while_t2_is_blocked(changelog: ChangeLog) -> None:
    store = ParsedStore()
    step = diff_step(
        changelog,
        parse_after_t2(
            parse_step(changelog), lambda r: store.record(r.event, r.parsed), store.drop
        ),
    )
    app = _create_app(EventLog(), DecisionLog(), store, changelog, "local", chunks=ChunkStore())
    client = TestClient(app)
    original = changelog.publish(upsert("HR-PL-04")).event
    step.handle(original)
    url = "/api/t3/parsed/document"
    query = {"document_key": KEY}
    assert client.get(url, params=query).status_code == 200

    changelog.store_original(KEY, "2.1", b"{}")
    broken = changelog.publish(upsert("HR-PL-04", "2.1")).event
    with pytest.raises(ParseError):
        step.handle(broken)
    deleted = changelog.publish(delete("HR-PL-04", "2.1")).event
    assert client.get(url, params=query).status_code == 404
    assert client.get("/api/t3/parsed").json()["documents"] == []

    pointer = changelog.store_original(KEY, "1.1", page_json("Replacement policy"))
    recreated = changelog.publish(upsert("HR-PL-04", raw_pointer=pointer)).event
    assert client.get(url, params=query).status_code == 404
    step.handle(deleted)
    step.handle(recreated)
    assert client.get(url, params=query).json()["text"] == "Replacement policy"
