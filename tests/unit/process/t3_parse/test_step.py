"""Running T3 for one event: the version the event names, from the raw store, or nothing."""

from __future__ import annotations

import pytest

from ekb.changelog.rawstore.options.in_memory import InMemoryRawStore
from ekb.changelog.rawstore.port import OriginalNotFoundError
from ekb.contracts import Acl, BlockKind
from ekb.process.t3_parse.options.markdown import MarkdownParser
from ekb.process.t3_parse.originals import docs_page
from ekb.process.t3_parse.port import ParseError
from ekb.process.t3_parse.step import ParseStep, summary
from tests.unit.changelog.factories import acl_change, delete, upsert
from tests.unit.process.t3_parse.conftest import page_json

KEY = "hr_docs:HR-PL-04"


@pytest.fixture
def rawstore() -> InMemoryRawStore:
    store = InMemoryRawStore()
    store.put(KEY, "1.1", page_json("# Remote work\n\nTwo days a week.\n", title="Remote work"))
    store.put(KEY, "2.1", page_json("# Remote work\n\nFriday too.\n", title="Remote work"))
    return store


@pytest.fixture
def step(rawstore: InMemoryRawStore) -> ParseStep:
    return ParseStep(MarkdownParser(), rawstore, {"hr_docs": docs_page})


def test_it_parses_the_version_the_event_names_not_the_latest(step: ParseStep) -> None:
    result = step.handle(upsert("HR-PL-04", "1.1"))
    assert result.parsed is not None
    assert (result.parsed.document_key, result.parsed.version) == (KEY, "1.1")
    assert [b.text for b in result.parsed.blocks] == ["Remote work", "Two days a week."]
    assert result.reason == f"parsed with {step.parser_id}: 2 blocks: 1 heading, 1 paragraph"


def test_structured_records_are_not_documents(step: ParseStep) -> None:
    """D7: HR records, rows and users are queried, not parsed. Their original isn't read."""
    result = step.handle(upsert("employees/E-1001", "2026-11-24T09:00:00", source="hr_api"))
    assert result.parsed is None
    assert result.reason.startswith("not a document: hr_api holds structured records")
    assert (step.handles("hr_docs"), step.handles("hr_api")) == (True, False)


@pytest.mark.parametrize(
    "event",
    [delete("HR-PL-04", "2.1"), acl_change("HR-PL-04", "1.2", Acl())],
    ids=["delete", "acl_change"],
)
def test_only_an_upsert_can_be_parsed(step: ParseStep, event: object) -> None:
    with pytest.raises(ValueError, match="upserts only"):
        step.handle(event)  # type: ignore[arg-type]


def test_a_missing_original_is_an_error_not_a_skip(step: ParseStep) -> None:
    with pytest.raises(OriginalNotFoundError):
        step.handle(upsert("HR-PL-04", "9.1"))


def test_a_broken_original_is_an_error_not_a_skip(rawstore: InMemoryRawStore) -> None:
    rawstore.put("hr_docs:HR-PL-07", "1.1", b"<html>not a page</html>")
    step = ParseStep(MarkdownParser(), rawstore, {"hr_docs": docs_page})
    with pytest.raises(ParseError, match="not a docs tool page"):
        step.handle(upsert("HR-PL-07", "1.1"))


def test_the_summary_counts_blocks_by_kind(step: ParseStep) -> None:
    parsed = step.handle(upsert("HR-PL-04", "2.1")).parsed
    assert parsed is not None
    assert summary(parsed) == "2 blocks: 1 heading, 1 paragraph"
    assert summary(parsed.model_copy(update={"blocks": ()})) == "0 blocks"
    assert parsed.blocks[0].kind is BlockKind.HEADING
