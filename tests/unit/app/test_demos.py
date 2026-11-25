"""``ekb demo s1 / s1b / s1c / s1d``: each runs end to end and tells the story it promises."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sources.mock_docs.app import create_app as docs_app
from sources.mock_hr_api.app import create_app as hr_api_app
from sources.mock_hr_db.db import create_database
from sources.mock_idp.app import create_app as idp_app
from typer.testing import CliRunner

from ekb.app.choices import choose, choose_query
from ekb.app.cli import app
from ekb.app.demos.ingest import demo_choices, run_ingest_demo
from ekb.app.demos.s1 import run_s1
from ekb.app.demos.s1b import run_s1b
from ekb.app.demos.s1c import run_s1c
from ekb.app.demos.s1d import run_s1d
from ekb.app.demos.s3 import run_s3_live, run_s3_replay
from ekb.app.demos.search import run_search_demo
from ekb.app.demos.t1 import run_t1, run_t1_all
from ekb.app.demos.t2 import run_t2_demo
from ekb.app.demos.t3 import run_t3_demo
from ekb.app.demos.t4 import run_t4_demo
from ekb.app.demos.t5 import close_cache, demo_gateway, run_t5_demo
from ekb.app.demos.t6 import run_t6_demo
from ekb.app.demos.t7 import demo_gateway as t7_gateway
from ekb.app.demos.t7 import run_t7_demo
from ekb.app.demos.t8 import run_t8_demo
from ekb.platform.config import load_settings
from tests.conftest import REPO_ROOT
from tests.http import as_httpx
from tests.unit.platform.gateway.helpers import make_world

Story = Callable[..., None]


def _in_process(make_app: Callable[[], FastAPI], story: Story, runs: int = 1) -> str:
    """Run a demo story against the mock in-process (a TestClient is an httpx.Client)."""
    lines: list[str] = []
    with TestClient(make_app()) as client:
        for _ in range(runs):
            lines.clear()
            story(client, echo=lines.append)
    return "\n".join(lines)


def test_s1_lists_documents_and_shows_each_kind_of_change() -> None:
    output = _in_process(docs_app, run_s1)
    assert "holds 12 synthetic HR documents" in output
    assert "Languages: en, es, it" in output
    assert "Restricted to hr-compensation: HR-CMP-01" in output
    assert "rev 1 -> 2" in output  # real edit
    assert "rev 2 -> 3" in output  # save without changes
    assert "(unchanged)" in output
    assert "acl_rev 1 -> 2, rev 1 -> 1" in output  # permissions only
    assert "page.permissions_changed" in output


def test_s1b_shows_pull_delta_and_silent_delete() -> None:
    output = _in_process(hr_api_app, run_s1b)
    assert "12 employees in 5 teams" in output
    assert "Per office: Madrid 3, Milan 6, Toronto 3" in output
    assert "-> E-1008" in output  # the delta holds only the moved record
    assert "E-1012 is not in the delta" in output
    assert "missing: E-1012" in output
    assert "409: employee E-1004 still manages" in output


def test_s1c_shows_membership_change_and_leaver() -> None:
    output = _in_process(idp_app, run_s1c)
    assert "12 users in 6 groups" in output
    assert "hr-compensation   1  bob" in output
    assert "groups all-staff, it-employees -> all-staff" in output
    assert "only through it-employees" in output
    assert "active=False" in output
    assert "group.member_removed   alice it-employees" in output
    assert "user.deactivated       olivia" in output
    assert output.count("group.member_removed") == 1  # the repeated removal emitted nothing


def test_s1c_replays_a_deactivation_during_initial_listing() -> None:
    service = idp_app()
    with TestClient(service) as client:
        lines: list[str] = []

        def echo(line: str) -> None:
            if line.startswith("1."):
                service.state.store.deactivate("bob")
            lines.append(line)

        run_s1c(as_httpx(client), echo=echo)
    assert any("user.deactivated" in line and "bob" in line for line in lines)


def test_s1d_shows_three_cdc_entries_and_no_rollback(tmp_path: Path) -> None:
    db_path = tmp_path / "hr.sqlite"
    create_database(db_path)
    lines: list[str] = []
    run_s1d(db_path, echo=lines.append)
    output = "\n".join(lines)
    assert "16 entries, all op=r" in output
    assert "#17  op=c  office_headcount  key office_id=LIS" in output
    assert "#18  op=u  leave_balances    key employee_id=E-1001, year=2026" in output
    assert "#19  op=d  office_headcount  key office_id=BLQ" in output
    assert "#20" not in output  # the rolled-back change left nothing


@pytest.mark.parametrize(
    ("make_app", "story"), [(docs_app, run_s1), (hr_api_app, run_s1b), (idp_app, run_s1c)]
)
def test_demos_are_repeatable_against_the_same_running_mock(
    make_app: Callable[[], FastAPI], story: Story
) -> None:
    once = _in_process(make_app, story)
    twice = _in_process(make_app, story, runs=2)  # each run resets the mock first
    assert twice.splitlines()[:5] == once.splitlines()[:5]


@pytest.mark.parametrize(
    ("step", "expected"),
    [
        ("s1", "holds 12 synthetic HR documents"),
        ("s1b", "12 employees in 5 teams"),
        ("s1c", "12 users in 6 groups"),
        ("s1d", "16 entries, all op=r"),
        ("t1", "full listing sync() -> 0 events"),
        ("t1 --all", "Unmapped people reported during the run: none."),
        ("s3", "Same as the registry: True."),
        ("t2", "T2 decided 19 events: process 14, acl_only 1, skip 2, purge 2."),
        ("t3", "T2 decided 16 events: process 13, acl_only 1, skip 1, purge 1."),
        ("t3 --doc HR-PL-09 --parser plain_text", "T3 ran 13 times"),
        ("t4", "Publishable chunks: 0 of 41."),
        ("t4 --chunker fixed --doc HR-CMP-01", "T3 and T4 ran 13 times"),
        ("t5", "Publishable chunks: 0 of 43"),
        ("t5 --fake --classifier model_plus_rules", "Labelled chunks: 43 of 43."),
        ("t5 --header none", "No model calls: this header option writes none."),
    ],
)
def test_cli_runs_each_demo_end_to_end(
    step: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    result = CliRunner().invoke(app, ["demo", *step.split()])
    assert result.exit_code == 0, result.output
    assert expected in result.output


@pytest.mark.parametrize("step", ["s1", "s1b", "s1c", "t1", "s3", "t2", "t3", "t4", "t5"])
def test_cli_reports_an_unreachable_mock_cleanly(step: str) -> None:
    result = CliRunner().invoke(app, ["demo", step, "--url", "http://127.0.0.1:9"])
    assert result.exit_code == 1
    assert "Demo failed" in result.output


def test_cli_s1d_reports_a_failed_start(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # sources/ is not here
    result = CliRunner().invoke(app, ["demo", "s1d"])
    assert result.exit_code == 1
    assert "repository root" in result.output


def _t1(runs: int = 1) -> str:
    lines: list[str] = []
    with TestClient(docs_app()) as docs, TestClient(idp_app()) as idp:
        for _ in range(runs):
            lines.clear()
            run_t1(as_httpx(docs), as_httpx(idp), echo=lines.append)
    return "\n".join(lines)


def test_t1_turns_four_changes_into_four_events_and_the_listing_catches_a_lost_delete() -> None:
    output = _t1(runs=2)  # a second run starts clean
    assert "4 notifications" in output
    assert "page.created              HR-PL-11  rev=1 acl_rev=1 actor=dana" in output
    assert "-> upsert     hr_docs:HR-PL-11" in output
    assert "-> acl_change hr_docs:HR-PL-05" in output
    assert "by=lucia  acl=es-employees" in output
    assert "-> delete     hr_docs:HR-PL-99" in output
    assert '"schema_version": 2' in output  # one event printed in full
    assert "delivered=4 failed=1" in output  # the receiver was down
    assert "webhook sync() -> 0 events" in output
    assert "HR-PL-11 known now: False" in output
    assert "full listing sync() -> 0 events" in output


def test_t1_leaves_no_subscription_behind() -> None:
    with TestClient(docs_app()) as docs, TestClient(idp_app()) as idp:
        run_t1(as_httpx(docs), as_httpx(idp), echo=lambda line: None)
        assert docs.get("/subscriptions").json() == []


def test_t1_all_shows_four_sources_in_one_shape(tmp_path: Path) -> None:
    db_path = tmp_path / "hr.sqlite"
    create_database(db_path)
    lines: list[str] = []
    with (
        TestClient(docs_app()) as docs,
        TestClient(hr_api_app()) as hr,
        TestClient(idp_app()) as idp,
    ):
        run_t1_all(as_httpx(docs), as_httpx(hr), as_httpx(idp), db_path, echo=lines.append)
    output = "\n".join(lines)
    assert "(by full name): 12/12" in output
    assert "Gaps: none." in output
    assert "first poll      17 items" in output
    assert "snapshot        16 items" in output
    assert "(1 + 1 + 1 + 1 events)" in output
    assert "hr_docs:HR-PL-04" in output
    assert "person=javier by=lucia  acl=all-staff" in output
    leave = "hr_db:leave_balances/E-1001/2026   v17       person=alice  by=dana"
    assert f"{leave}   acl=hr-team,user:alice" in output
    assert "identity:users/alice               v1        person=alice  by=bob" in output
    assert "delta sync() -> 0 events" in output
    assert "-> delete     hr_api:employees/E-1012" in output
    assert "Unmapped people reported during the run: none." in output


def _s3(runs: int = 1) -> str:
    lines: list[str] = []
    with TestClient(docs_app()) as docs, TestClient(idp_app()) as idp:
        for _ in range(runs):
            lines.clear()
            changelog = run_s3_live(as_httpx(docs), as_httpx(idp), echo=lines.append)
            assert docs.get("/subscriptions").json() == []  # no subscription left behind
    run_s3_replay(changelog, "The docs tool has been stopped.", echo=lines.append)
    return "\n".join(lines)


def test_s3_publishes_records_redelivers_and_replays_without_the_source() -> None:
    output = _s3(runs=2)  # a second run starts clean
    assert "full listing sync() -> 12 events published" in output
    assert "partition 0:  6 events  HR-CMP-01 HR-PL-02 HR-PL-04" in output
    assert "raw store: 12 originals, e.g. mem://ekb-raw/hr_docs/HR-PL-04/1.1" in output
    assert "group 'registry' has 12 events pending" in output
    assert "recorded 12 events; the registry knows 12 pages; pending now 0." in output
    assert "full listing sync() again -> 0 events" in output
    # Order per document: both edits in one partition, in order.
    assert "p0@6   upsert     HR-PL-04  v2.1  by=dana" in output
    assert "p1@3   acl_change HR-PL-05  v1.2  by=lucia  readers=es-employees" in output
    assert "p0@7   upsert     HR-PL-04  v3.1" in output
    assert "p0@8   delete     HR-PL-99  v1.1  by=dana   readers=-" in output
    # At least once, and no skipping past a pending event.
    assert "After a restart, poll -> 3 events again: p1@3, p0@7, p0@8." in output
    assert "Committing the delete (p0@8) before p0@7 is refused" in output
    assert "accepted (it must not be)" not in output
    # The registry: an ACL change keeps the original; a delete leaves a tombstone.
    assert "HR-PL-05  v1.2  live    readers=es-employees  hash=" in output
    assert "original=hr_docs/HR-PL-05/1.1" in output
    assert "HR-PL-99  v1.1  deleted readers=-             hash=-        original=-" in output
    # Replay from the raw store only.
    assert "16 events replayed; 14 originals read from the raw store" in output
    assert "Rebuilt: 11 live pages, 1 tombstone. Same as the registry: True." in output
    assert "Consumer groups: registry pending 0, reindex pending 0." in output


def _t2(runs: int = 1) -> str:
    lines: list[str] = []
    with TestClient(docs_app()) as docs, TestClient(idp_app()) as idp:
        for _ in range(runs):
            lines.clear()
            changelog = run_t2_demo(as_httpx(docs), as_httpx(idp), echo=lines.append)
            assert docs.get("/subscriptions").json() == []  # no subscription left behind
    assert changelog.rawstore.versions("hr_docs:HR-PL-99") == []
    return "\n".join(lines)


def test_t2_skips_repeats_fast_tracks_a_revocation_and_purges_deletes() -> None:
    output = _t2(runs=2)  # a second run starts clean
    assert "T2: {'process': 12} (reason: 'new: never processed')" in output
    # Reported twice: the webhook's copy is processed, the listing's copy is a repeat.
    assert "p0@6   upsert     HR-PL-04  v2.1  -> process  content changed: hash " in output
    assert "p0@7   upsert     HR-PL-04  v2.1  -> skip     repeat: v2.1 was already processed" in (
        output
    )
    # Same text, new version: skipped, and the listing stays quiet.
    assert "HR-PL-01  v2.1  -> skip     new version, same content and readers as v1.1" in output
    assert "so the full listing stays quiet: sync() -> 0 events." in output
    # Revocation: the fast lane.
    assert "acl_change HR-PL-05  v1.2  -> acl_only readers changed (-hr-team)" in output
    # Delete with a queued re-creation: the old original goes, the new one stays.
    assert "HR-PL-99's originals in the raw store: 1.1 before, 1.1, 2.1" in output
    assert "delete     HR-PL-99  v1.1  -> purge    deleted in the source" in output
    assert "remove it from every store; 1 original purged" in output
    assert "upsert     HR-PL-99  v2.1  -> process  back after a delete (v1.1)" in output
    assert "Originals left: 2.1." in output
    assert "Originals left: none. Registry: tombstone" in output
    assert "T2 decided 19 events: process 14, acl_only 1, skip 2, purge 2." in output
    assert "With it, 14 do: 3 runs avoided." in output
    assert "Consumer groups: t2_diff pending 0." in output


def _t3(runs: int = 1, **options: str) -> str:
    lines: list[str] = []
    with TestClient(docs_app()) as docs, TestClient(idp_app()) as idp:
        for _ in range(runs):
            lines.clear()
            story = run_t3_demo(as_httpx(docs), as_httpx(idp), echo=lines.append, **options)
            assert docs.get("/subscriptions").json() == []  # no subscription left behind
    assert "hr_docs:HR-PL-99" not in story.latest
    return "\n".join(lines)


def test_t3_follows_a_page_keeps_tables_and_parses_only_changes() -> None:
    output = _t3(runs=2)  # a second run starts clean
    assert "full listing sync() -> 12 events" in output
    assert "HR-PL-01   v1.1  13 blocks: 5 heading, 2 paragraph, 6 list_item" in output
    assert "HR-CMP-01  v1.1  8 blocks: 4 heading, 2 paragraph, 2 table" in output
    # One page, end to end: bytes -> body -> blocks with their section.
    assert "Its fields: id, title, language, department, acl, rev, acl_rev" in output
    assert "   | ## Booking leave" in output
    assert "list_item 1.   Request leave in the HR portal" in output
    assert "sits under: Annual leave policy > How much leave you get" in output
    assert "'**25 working days**' in the Markdown is '25 working days' in the text" in output
    # Tables survive as tables.
    assert "Engineering \N{EN DASH} Italy (gross annual, EUR): 4 rows x 4 columns" in output
    assert "E2       | 40,000   | 47,000   | 54,000" in output
    # The baseline loses the structure.
    assert "plain_text  5 blocks: 5 paragraph" in output
    assert "glued to the paragraph below; 0 blocks" in output
    # Only changes are parsed again; the deleted page's parsed text goes.
    assert "HR-PL-04 v2.1: T2 process -> T3 parsed with markdown-it-py@" in output
    assert "HR-PL-01 v2.1: T2 skip -> T3 not run (same text: same blocks)" in output
    assert "HR-PL-05 v1.2: T2 acl_only -> T3 not run (only the readers changed" in output
    assert "HR-PL-99 v1.1: T2 purge -> T3 not run; its parsed text is forgotten" in output
    assert "Parsed documents kept: 11." in output
    assert "T3 ran 13 times: once per 'process', never for the others." in output


def test_t3_can_follow_another_page_with_the_baseline_parser() -> None:
    output = _t3(doc="HR-PL-09", parser_name="plain_text")
    assert "1. Initial load: full listing -> T2 -> T3 (plain_text@1)." in output
    assert "2. One page, end to end: HR-PL-09 v1.1" in output


def test_t3_shows_a_section_on_a_page_without_list_items() -> None:
    output = _t3(doc="HR-CMP-01")
    assert "sits under: Salary bands 2026 (restricted)" in output


def test_cli_t3_rejects_an_unknown_parser_or_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    bad_parser = CliRunner().invoke(app, ["demo", "t3", "--parser", "docling"])
    assert bad_parser.exit_code == 2
    assert "markdown, plain_text" in bad_parser.output
    bad_page = CliRunner().invoke(app, ["demo", "t3", "--doc", "HR-XX-00"])
    assert bad_page.exit_code == 1
    assert "no page 'HR-XX-00'" in bad_page.output


def _t4(runs: int = 1, **options: object) -> str:
    lines: list[str] = []
    with TestClient(docs_app()) as docs, TestClient(idp_app()) as idp:
        for _ in range(runs):
            lines.clear()
            story = run_t4_demo(docs, idp, echo=lines.append, **options)  # type: ignore[arg-type]
            assert docs.get("/subscriptions").json() == []  # no subscription left behind
    assert "hr_docs:HR-PL-99" not in story.chunks
    assert not any(chunk.publishable for chunk in story.kept())
    return "\n".join(lines)


def test_t4_follows_a_policy_compares_the_baseline_and_chunks_only_changes() -> None:
    output = _t4(runs=2)  # a second run starts clean
    assert (
        "1. Initial load: full listing -> T2 -> T3 -> T4 (structure_aware@1(max_tokens=400))."
        in output
    )
    assert "HR-PL-01   v1.1  13 blocks -> 4 chunks, 38-59 tokens" in output
    assert "48 chunks from 12 pages; 0 over the 400-token limit." in output
    # One policy, chunk by chunk.
    assert "hr_docs:HR-PL-01#2  49 tokens  § Annual leave policy > Booking leave" in output
    assert "   | 2. Your manager approves or declines within three working days." in output
    assert "'Annual leave policy' has no text of its own: no chunk" in output
    # The baseline: one chunk per page at 400; cut tables at 60.
    assert "fixed@1(max_tokens=400,overlap_tokens=50): 12 chunks for 12 pages." in output
    assert "hr_docs:HR-CMP-01#1 starts in the middle of a line: '| 47,000 | 54,000'." in output
    assert "hr_docs:HR-CMP-01#1 holds 1 row(s) of the 'Engineering \N{EN DASH} Italy" in output
    assert "'E3 | 52,000 | 61,000 | 70,000' no longer" in output
    assert "structure_aware@1(max_tokens=400) keeps each table whole, under its heading:" in output
    # The header that S8 will add.
    assert "hr_docs:HR-PL-01#3 reads: 'You may carry up to 5 unused days" in output
    assert "It never says 'Annual leave policy'." in output
    # Only changes are chunked again; the deleted page's chunks go.
    assert (
        "HR-PL-04 v2.1: T2 process -> T3 3 blocks: 2 heading, 1 paragraph -> T4 1 chunk" in output
    )
    assert "HR-PL-01 v2.1: T2 skip -> T3, T4 not run (same text: same chunks)" in output
    assert "HR-PL-05 v1.2: T2 acl_only -> T3, T4 not run (only the readers changed" in output
    assert "HR-PL-99 v1.1: T2 purge -> T3, T4 not run; its chunks are forgotten" in output
    assert "Chunks kept: 41 from 11 pages." in output
    assert "T3 and T4 ran 13 times: once per 'process', never for the others." in output
    assert "Publishable chunks: 0 of 41." in output


def test_t4_can_run_the_story_with_the_baseline_and_another_page() -> None:
    output = _t4(doc="HR-CMP-01", chunker_name="fixed")
    assert "T4 (fixed@1(max_tokens=400,overlap_tokens=50))" in output
    assert "HR-CMP-01  v1.1   8 blocks -> 1 chunk, 149 tokens" in output
    assert "hr_docs:HR-CMP-01#0  149 tokens  § (no section)" in output
    assert "With this chunker the chunks carry no section" in output
    assert "structure_aware@1(max_tokens=400) keeps each table whole" in output  # still compared


def test_t4_shows_a_section_split_when_the_limit_is_small() -> None:
    output = _t4(max_tokens=30)
    assert "over the 30-token limit" in output  # the salary tables are kept whole
    assert "structure_aware@1(max_tokens=30)" in output
    assert "Continuation chunks carry the same heading trail as metadata." in output


def test_cli_t4_rejects_an_unknown_chunker_bad_settings_or_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    bad_chunker = CliRunner().invoke(app, ["demo", "t4", "--chunker", "semantic"])
    assert bad_chunker.exit_code == 2
    assert "fixed, structure_aware" in bad_chunker.output
    too_small = CliRunner().invoke(app, ["demo", "t4", "--chunker", "fixed", "--max-tokens", "40"])
    assert too_small.exit_code == 2
    assert "overlap_tokens" in too_small.output
    bad_page = CliRunner().invoke(app, ["demo", "t4", "--doc", "HR-XX-00"])
    assert bad_page.exit_code == 1
    assert "no page 'HR-XX-00'" in bad_page.output


def _t5(runs: int = 1, **options: str) -> str:
    lines: list[str] = []
    with TestClient(docs_app()) as docs, TestClient(idp_app()) as idp:
        gateway, _ = demo_gateway(fake=True)  # one gateway for every run: its cache is kept
        for _ in range(runs):
            lines.clear()
            story = run_t5_demo(as_httpx(docs), as_httpx(idp), gateway, lines.append, **options)
            assert docs.get("/subscriptions").json() == []  # no subscription left behind
    assert "hr_docs:HR-PL-99" not in story.processed
    assert not any(chunk.publishable for chunk in story.kept())
    assert all(chunk.sensitivity is not None for chunk in story.kept())
    return "\n".join(lines)


def test_t5_writes_headers_labels_every_chunk_and_explains_why() -> None:
    output = _t5()
    assert "Header: model@1(header@1). Classifier: rules@1." in output
    assert "HR-CMP-01  4 chunks  headers written  4 restricted" in output
    assert "48 chunks from 12 pages; 48 have a header." in output
    assert "Model calls so far: 12 requests, 0 from the cache." in output
    # One chunk, before and after.
    assert "2. One chunk, before and after its header: hr_docs:HR-PL-01#3." in output
    assert "header:  fake-small chunk_3" in output
    assert "by:      model@1(header@1, fake:fake-small)" in output
    # Labels and why.
    assert "HR-PL-06   finance  (source)  5 internal" in output
    assert "restricted: the document says “(restricted)”" in output
    assert "hr_docs:HR-PL-10#2  says 'confidential' -> internal" in output
    # Personal data, only the IBAN's chunk raised.
    assert "hr_docs:HR-PL-11#0  internal, people, pii: email, phone" in output
    assert "hr_docs:HR-PL-11#1  confidential, people, pii: iban" in output
    # The replay comes from the cache; changes; the purge forgets the replies.
    assert "6. A replay: T4 again on all 13 pages, as a rebuild would." in output
    assert "   0 requests, 13 from the cache." in output
    assert "HR-PL-04 v2.1: T2 process -> T3, T4 1 chunk(s), headers written -> T5 1 internal" in (
        output
    )
    assert "HR-PL-01 v2.1: T2 skip -> T3, T4, T5 not run" in output
    assert "HR-PL-05 v1.2: T2 acl_only -> T3, T4, T5 not run (labels come from the text)" in output
    assert "HR-PL-99 v1.1: T2 purge -> chunks forgotten, and 1 cached model reply" in output
    assert "T4's header and T5 ran 14 times" in output
    assert "Model calls in this run: 14 requests, 13 from the cache." in output
    assert "Publishable chunks: 0 of 43" in output


def test_t5_a_second_run_finds_the_unchanged_headers_in_the_cache() -> None:
    output = _t5(runs=2)
    # HR-PL-99 was forgotten when it was deleted; it is re-created, so it costs a request.
    # Every other page's header is still cached, and the edit of HR-PL-04 too.
    assert "Model calls so far: 1 requests, 11 from the cache." in output
    assert "HR-PL-04 v2.1: T2 process -> T3, T4 1 chunk(s), headers cached" in output


def test_t5_with_the_model_classifier_and_without_a_header() -> None:
    output = _t5(header="none", classifier="model_plus_rules")
    assert "Header: none@1. Classifier: model_plus_rules@1(classify@2, rules@1)." in output
    assert "header:  '' (no header: the none option writes none)" in output
    assert "HR-PL-99 v1.1: T2 purge -> chunks forgotten, and 1 cached model reply" in output


def test_t5_without_any_key_runs_with_empty_headers_and_says_why(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EKB_PROFILE", "local")
    monkeypatch.chdir(tmp_path)  # the local profile's cache files go here, not in the repo
    for key in ("GOOGLE_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    gateway, warning = demo_gateway(fake=False)
    assert warning == "no model provider has a key: headers will be empty"
    lines: list[str] = []
    try:
        with TestClient(docs_app()) as docs, TestClient(idp_app()) as idp:
            run_t5_demo(as_httpx(docs), as_httpx(idp), gateway, lines.append)
    finally:
        close_cache(gateway)
    output = "\n".join(lines)
    assert "HR-PL-01   4 chunks  headers failed   4 internal" in output
    assert "Set a free API key" in output
    assert "No model calls: no model is available, so nothing was cached." in output


def test_cli_t5_rejects_unknown_options() -> None:
    bad_header = CliRunner().invoke(app, ["demo", "t5", "--header", "sonnet"])
    assert bad_header.exit_code == 2
    assert "none, model" in bad_header.output
    bad_classifier = CliRunner().invoke(app, ["demo", "t5", "--classifier", "presidio"])
    assert bad_classifier.exit_code == 2
    assert "rules, model_plus_rules" in bad_classifier.output


def _t6() -> str:
    lines: list[str] = []
    with (
        TestClient(docs_app()) as docs,
        TestClient(hr_api_app()) as hr,
        TestClient(idp_app()) as idp,
    ):
        story = run_t6_demo(as_httpx(docs), as_httpx(hr), as_httpx(idp), lines.append)
        assert docs.get("/subscriptions").json() == []  # no subscription left behind
    assert "hr_docs:HR-PL-12" not in story.processed
    assert story.store.get("person:lucia") is None
    assert not any(chunk.publishable for chunk in story.kept())
    return "\n".join(lines)


def test_t6_fills_the_store_from_hr_records_and_links_pages_with_the_words_behind_them() -> None:
    output = _t6()
    assert "Linker: exact_match@2. Store: in memory." in output
    # The entities, under their identity user IDs.
    assert "HR delta sync() -> 17 events (the first poll: every record)" in output
    assert "17 entities added:" in output
    assert "person:bob     Bruno Moretti   bruno.moretti@example.com   team:T-PPL" in output
    assert "The HR system calls Bruno E-1002; his entity ID is person:bob" in output
    # The seed pages: teams only, each with its words.
    assert "HR-PL-04   2 of 4 chunks linked: team:T-PPL x2" in output
    assert "6 of 48 chunks are linked; 0 of them to a person." in output
    assert "hr_docs:HR-CMP-01#3  team:T-PPL    team  'Head of People'" in output
    assert "hr_docs:HR-PL-07#2   team:T-PPL    team  'equipo de People'" in output
    # Near misses.
    assert "'compensation team': a team word, but no team of that name" in output
    assert "'Engineering': a team's name with no team word" in output
    # The contacts page: names, accents, an email; not a first name.
    assert "hr_docs:HR-PL-12#1   person:lucia  name  'Lucia Navarro'" in output
    assert "person:dana   email 'daniela.ferri@example.com'" in output
    assert "person:marco" not in output.split("5. dana creates")[1].split("6.")[0]


def test_t6_a_leaver_found_by_the_full_listing_leaves_the_store_and_the_next_version() -> None:
    output = _t6()
    assert "HR delta sync()        -> 0 events (a delta never reports one)" in output
    assert "employees/E-1009: T2 purge -> entity removed" in output
    assert "person:lucia in the entity store: no" in output
    assert "HR-PL-12 still links her on: hr_docs:HR-PL-12#1" in output  # the stated limit
    assert "hr_docs:HR-PL-12#1   no links" in output  # after the edit
    assert "HR-PL-12 v2.1: T2 purge" in output
    assert "its chunks, and the links on them, kept: no" in output
    assert "Entity store: 11 people, 5 teams." in output
    assert "Publishable chunks: 0 of 48" in output


def test_cli_t6_is_registered() -> None:
    result = CliRunner().invoke(app, ["demo", "t6", "--help"])
    assert result.exit_code == 0
    assert "T6 Entities" in result.output


def _t7() -> str:
    lines: list[str] = []
    with TestClient(docs_app()) as docs, TestClient(idp_app()) as idp:
        gateway, warning = t7_gateway(fake=True)
        assert warning is None
        story = run_t7_demo(as_httpx(docs), as_httpx(idp), gateway, lines.append)
        assert docs.get("/subscriptions").json() == []  # no subscription left behind
    assert "hr_docs:HR-PL-99" not in story.processed
    assert all(chunk.vector is not None for chunk in story.kept())
    assert not any(chunk.publishable for chunk in story.kept())
    return "\n".join(lines)


def test_t7_embeds_every_chunk_and_says_when_the_model_is_a_fake() -> None:
    output = _t7()
    assert "Embedder: model@1(input@1, fake:fake-embed, 1024)" in output
    assert "FAKE MODEL" in output
    assert "With the fake model every pair is close to 0" in output
    assert "     A    1.00" in output  # a text points the same way as itself
    assert "HR-PL-01   4 chunks  4 sent, 0 from the cache" in output
    assert "48 of 48 chunks from 12 pages have a vector." in output
    assert "3. One chunk and its vector: hr_docs:HR-PL-01#3." in output
    assert "embedding_model: model@1(input@1, fake:fake-embed, 1024)" in output
    assert "publishable:     False  (sensitivity internal, vector yes, ACL no)" in output


def test_t7_an_edit_sends_one_chunk_a_revoke_none_and_a_delete_forgets_its_vectors() -> None:
    output = _t7()
    assert "HR-PL-01 v2.1: T2 process -> T3-T7: 1 chunk(s) sent, 3 from the cache" in output
    assert "HR-PL-05 v1.2: T2 acl_only -> T3-T7 not run" in output
    assert "HR-PL-99 v1.1: T2 purge -> purged: chunks and 4 cached vectors forgotten" in output
    assert "Against today's model: current" in output
    assert "Against model@1(input@1, bedrock:cohere.embed-v4:0, 1024, int8): stale" in output
    assert "would make all 44 chunks stale" in output
    assert "T7 ran 13 times: once per 'process'" in output
    assert "Publishable chunks: 0 of 44" in output


def test_t7_falls_back_to_the_fake_when_the_configured_model_cannot_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ekb.app.demos.t7.probe", lambda gateway: "ollama:bge-m3: unavailable")

    gateway, warning = t7_gateway(fake=False)

    assert warning is not None
    assert "ollama:bge-m3: unavailable" in warning
    assert gateway.embedding_model("embed") == ("fake:fake-embed", 1024)


def test_cli_t7_is_registered() -> None:
    result = CliRunner().invoke(app, ["demo", "t7", "--help"])
    assert result.exit_code == 0
    assert "T7 Embed" in result.output


def _t8() -> str:
    lines: list[str] = []
    with TestClient(docs_app()) as docs, TestClient(idp_app()) as idp:
        story = run_t8_demo(as_httpx(docs), as_httpx(idp), lines.append)
        assert docs.get("/subscriptions").json() == []  # no subscription left behind
    chunks = story.index.chunks()
    assert chunks
    assert all(chunk.publishable for chunk in chunks)
    assert "hr_docs:HR-PL-99" not in story.index.documents()
    assert story.held == []
    return "\n".join(lines)


def test_t8_publishes_every_page_with_its_readers_and_nothing_before_t8_has_run() -> None:
    output = _t8()
    assert "identity sync() -> 12 user records -> 12 active memberships" in output
    assert "HR-CMP-01  4 chunks  readers hr-compensation             1 person" in output
    assert "48 chunks published from 12 pages; 48 of 48 have readers" in output
    assert "In the search store: 0 chunks of HR-PL-11." in output
    assert "bob is in hr-team, and finds nothing of it." in output
    assert "refused: hr_docs:HR-PL-11#0 is not publishable: missing" in output
    assert "Who finds it now: bob, dana, lucia. alice, not in hr-team, finds nothing." in output


def test_t8_a_group_change_a_leaver_a_revocation_and_a_delete() -> None:
    output = _t8()
    assert "users/alice: T2 process -> alice: -it-employees" in output
    assert "lost HR-PL-02, HR-PL-03, HR-PL-08." in output
    assert "Chunks rewritten: 0. Texts embedded: 0." in output
    assert "olivia left: reads nothing now (keeps 2 groups)" in output
    assert "and finds 0 chunks (was 25)" in output
    assert "HR-PL-05 v1.2: T2 acl_only -> T8 fast lane" in output
    assert "readers -hr-team on 4 published chunks." in output
    assert "After:  carla, javier, lucia." in output
    assert "Texts embedded: 0; texts unchanged: True. bob, dana lost it" in output
    assert "HR-PL-99: T2 purge -> 4 chunks out of the search store" in output
    assert "Who finds it now: no one. Readers record: gone." in output
    assert "T2 decided 29 events: process 27, acl_only 1, skip 0, purge 1." in output
    assert "Search store: 46 chunks from 12 pages, 46 of them publishable" in output


def test_cli_t8_runs_offline_and_reports_an_unreachable_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    result = CliRunner().invoke(app, ["demo", "t8"])
    assert result.exit_code == 0, result.output
    assert "nothing without readers, a sensitivity and a vector is in it." in result.output
    result = CliRunner().invoke(app, ["demo", "t8", "--url", "http://127.0.0.1:9"])
    assert result.exit_code == 1
    assert "Demo failed" in result.output


def _ingest(tmp_path: Path) -> str:
    lines: list[str] = []
    create_database(tmp_path / "hr.sqlite")
    choices = choose(load_settings(profile="local"), offline=True)
    with (
        TestClient(docs_app()) as docs,
        TestClient(hr_api_app()) as hr,
        TestClient(idp_app()) as idp,
    ):
        story = run_ingest_demo(
            as_httpx(docs),
            as_httpx(hr),
            as_httpx(idp),
            tmp_path / "hr.sqlite",
            lines.append,
            choices=choices,
        )
        assert docs.get("/subscriptions").json() == []  # no subscription left behind
    chunks = story.stores.search.chunks()
    assert chunks
    assert all(chunk.publishable for chunk in chunks)
    assert "hr_docs:HR-PL-99" not in story.stores.search.documents()
    assert len(story.pipeline.dead_letters) == 0
    return "\n".join(lines)


def test_ingest_names_the_options_and_loads_every_source(tmp_path: Path) -> None:
    output = _ingest(tmp_path)
    assert "profile local, offline (--models calls them):" in output
    assert "t3_parse        docling -> markdown (not built yet)" in output
    assert "t7_embed        model -> fake (offline: no model)" in output
    assert "t4_chunk        structure_aware(max_tokens=400)" in output
    assert "The connectors put 57 events on the bus (3 partitions)" in output
    assert "(57 attempts, none failed)" in output
    assert "identity  12 users    -> permission store: 12 active memberships" in output
    assert "hr_api    17 records  -> entity store: 12 people, 5 teams" in output
    assert "hr_db     16 rows     -> no store yet: tables for SQL" in output
    assert "hr_docs   12 pages    -> search store: 48 chunks, 48 publishable" in output
    assert "Pages each person can find: alice 9, bob 12," in output


def test_ingest_follows_one_page_and_each_kind_of_change(tmp_path: Path) -> None:
    output = _ingest(tmp_path)
    assert "2. One page end to end: HR-PL-04 v1.1" in output
    assert "T8 publish" in output
    assert "Searchable now for 12 people." in output
    full = "T2 process -> T8 readers first -> T3 -> T4 -> T5 -> T6 -> T7 -> T8 publish = published"
    assert full in output
    assert "T2 acl_only -> T8 fast lane = readers" in output
    assert "Vectors unchanged: True. Who finds it now: carla, javier, lucia." in output
    assert "T2 skip = skipped" in output
    assert "T2 process -> T3 -> permission store = record" in output
    assert "alice: -it-employees; chunks rewritten: 0." in output
    assert "T2 purge -> purge search -> purge entity = purged" in output
    assert "4 chunks out of the search store" in output


def test_ingest_parks_a_poison_event_and_a_retry_republishes_it(tmp_path: Path) -> None:
    output = _ingest(tmp_path)
    assert "run 1: hr_docs:HR-PL-06 v2.1: attempt 1 of 3 failed at T7 embed" in output
    assert "run 3: parked after 3 failed attempts; withdrawn until it is retried" in output
    assert "HR-PL-09 -> published (lag 0)." in output
    assert "Who finds HR-PL-06 now: no one (was 12 people)." in output
    assert "parked behind v2.1 (2 parked for this item)" in output
    assert "v2.2: T2 acl_only -> T8 fast lane = readers" in output
    assert "Who finds HR-PL-06 now: bob, dana, lucia." in output
    assert "Group worker lag 0; dead letters 0; search store: 34 chunks from 11 pages" in output


def test_ingest_with_models_falls_back_to_fake_vectors_when_t7_cannot_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = make_world().gateway  # no SQLite cache or cost log under .cache/
    monkeypatch.setattr("ekb.app.demos.ingest.build_gateway", lambda config: fake)
    monkeypatch.setattr("ekb.app.demos.ingest.probe", lambda gateway: "ollama: unavailable")
    choices, gateway, note = demo_choices(load_settings(profile="local"), models=True)
    assert gateway is fake
    assert choices.option("t7_embed") == "fake"
    assert choices.option("t4_header") == "model"
    assert "ollama: unavailable" in note
    offline, none, quiet = demo_choices(load_settings(profile="local"), models=False)
    assert (offline.calls_models, none, quiet) == (False, None, "")


def test_cli_ingest_runs_offline_and_reports_an_unreachable_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setenv("EKB_PROFILE", "local")
    result = CliRunner().invoke(app, ["demo", "ingest"])
    assert result.exit_code == 0, result.output
    assert "Group worker lag 0; dead letters 0" in result.output
    result = CliRunner().invoke(app, ["demo", "ingest", "--url", "http://127.0.0.1:9"])
    assert result.exit_code == 1
    assert "Demo failed" in result.output


def _search(tmp_path: Path, *, models: bool = False) -> str:
    """``ekb demo search`` in-process; with ``models``, T7 runs a (fake) model, so both legs do."""
    lines: list[str] = []
    create_database(tmp_path / "hr.sqlite")
    settings = load_settings(profile="local")
    write = choose(settings, offline=True)
    gateway = None
    if models:
        write = write.with_options("test", t7_embed="model")
        gateway = make_world().gateway
    with (
        TestClient(docs_app()) as docs,
        TestClient(hr_api_app()) as hr,
        TestClient(idp_app()) as idp,
    ):
        world = run_search_demo(
            as_httpx(docs),
            as_httpx(hr),
            as_httpx(idp),
            tmp_path / "hr.sqlite",
            lines.append,
            write=write,
            query=choose_query(settings),
            gateway=gateway,
        )
    assert len(world.pipeline.dead_letters) == 0
    return "\n".join(lines)


def test_search_names_the_options_and_who_can_read_what(tmp_path: Path) -> None:
    output = _search(tmp_path)
    assert "q2_prefilter acl_and_cedar -> acl (not built yet)" in output
    assert "q3_retrieve hybrid_rrf(top_k=5)" in output
    assert "q3_rerank bge_reranker -> none (not built yet)" in output
    assert "questions not embedded: t7_embed runs fake vectors" in output
    assert "Search store: 48 chunks from 12 pages." in output
    assert "alice   reads as alice + all-staff, it-employees" in output
    assert "searches 36 chunks of 9 pages; the other 12 are never scored for alice." in output
    assert "searches all 48 chunks of 12 pages." in output  # bob


def test_search_answers_alice_and_bob_from_what_each_may_read(tmp_path: Path) -> None:
    output = _search(tmp_path)
    salary = output.split('2.1 "')[1].split('2.2 "')[0]
    alice, bob = salary.split("asked by bob")
    assert "HR-CMP-01#" not in alice.split("inspector view")[0]  # never in her results
    assert "the pre-filter kept out 4 chunks sharing a word" in alice
    assert "HR-CMP-01 (4). alice never learns they exist." in alice
    assert "1. HR-CMP-01#1" in bob
    assert "note: keyword leg only: no embedding model for questions" in output
    law = output.split('2.3 "')[1]
    assert "1. HR-PL-05#1" in law.split("asked by bob")[1]  # hr-team reads the Madrid page


def test_search_follows_a_group_change_and_finds_nothing_for_nobody(tmp_path: Path) -> None:
    output = _search(tmp_path)
    assert "before  HR-CMP-01#1, HR-CMP-01#3" in output
    after = output.split("after   ")[1].split("\n")[0]
    assert "HR-CMP-01" not in after
    assert "Chunks rewritten: 0." in output
    assert "olivia   inactive (has left): reads nothing" in output
    assert "0 hits: nothing was searched: olivia reads nothing." in output
    assert "mallory  unknown to the identity provider: reads nothing" in output


def test_search_with_a_model_runs_both_legs_and_fuses_them(tmp_path: Path) -> None:
    output = _search(tmp_path, models=True)
    assert "questions embedded by T7's model: model@1(input@1, fake:vec, 8)" in output
    assert "     vector   HR-" in output
    assert "keyword leg only" not in output


def test_cli_search_runs_offline_and_reports_an_unreachable_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setenv("EKB_PROFILE", "local")
    result = CliRunner().invoke(app, ["demo", "search", "annual leave", "--user", "carla"])
    assert result.exit_code == 0, result.output
    assert '2.1 "annual leave"' in result.output
    assert "asked by carla" in result.output
    assert "asked by alice" not in result.output
    result = CliRunner().invoke(app, ["demo", "search", "--url", "http://127.0.0.1:9"])
    assert result.exit_code == 1
    assert "Demo failed" in result.output
