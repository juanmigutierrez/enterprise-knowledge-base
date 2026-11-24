"""``ekb inspector``: the connectors run in rounds against the mocks; the command is local only."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sources.mock_docs.app import create_app as docs_app
from sources.mock_hr_api.app import create_app as hr_api_app
from sources.mock_hr_db.db import create_database
from sources.mock_idp.app import create_app as idp_app
from typer.testing import CliRunner

from ekb.app.changelog import ChangeLog
from ekb.app.cli import app
from ekb.app.inspector import SyncLoop, build_connectors
from ekb.contracts import Acl, ChangeOp, DiffAction
from ekb.contracts.listing import NeedsOriginal
from ekb.operate.inspector.log import EventLog
from tests.unit.changelog.factories import acl_change


@pytest.fixture
def clients() -> Iterator[tuple[TestClient, TestClient, TestClient]]:
    with (
        TestClient(docs_app(post=lambda url, payload: None)) as docs,
        TestClient(hr_api_app()) as hr,
        TestClient(idp_app()) as idp,
    ):
        yield docs, hr, idp


def test_round_zero_loads_every_source_once(
    clients: tuple[TestClient, TestClient, TestClient], tmp_path: Path
) -> None:
    docs, hr, idp = clients
    create_database(tmp_path / "hr.sqlite")
    log, changelog = EventLog(), ChangeLog.in_memory()
    connectors = build_connectors(docs, hr, idp, tmp_path / "hr.sqlite", changelog)
    loop = SyncLoop(connectors, changelog, log, listing_every=2)
    assert loop.run_round() == 12 + 17 + 16 + 12  # pages, HR records, DB rows, users
    # T2 recorded everything before the listings compared with the registry, so the
    # HR listing added nothing on top of the delta's first poll.
    registry = changelog.registry
    assert {s: len(registry.known(s)) for s in ("hr_docs", "hr_api", "hr_db", "identity")} == {
        "hr_docs": 12,
        "hr_api": 17,
        "hr_db": 16,
        "identity": 12,
    }
    assert log.cursor == 57  # the inspector's group has read every event
    assert {g.group: g.lag for g in changelog.bus.stats().groups} == {
        "inspector": 0,
        "t2_diff": 0,
    }
    assert loop.decisions.counts() == {
        DiffAction.PROCESS: 57,  # everything is new
        DiffAction.ACL_ONLY: 0,
        DiffAction.SKIP: 0,
        DiffAction.PURGE: 0,
    }
    assert len(changelog.rawstore.versions("hr_docs:HR-PL-04")) == 1
    assert loop.run_round() == 0  # nothing changed

    hr.delete("/employees/E-1012")
    assert loop.run_round() == 2  # leave ACL revocation, then the HR listing delete
    revoked = log.entries(since=log.cursor - 2)[0].event
    assert revoked.op is ChangeOp.ACL_CHANGE
    assert revoked.acl.users == frozenset()
    (last,) = log.entries(since=log.cursor - 1)
    assert (last.event.op, last.event.source_id) == (ChangeOp.DELETE, "employees/E-1012")
    gone = registry.get("hr_api:employees/E-1012")
    assert gone is not None
    assert gone.deleted
    assert "employees/E-1012" not in registry.known("hr_api")
    # T2: the revocation took the fast lane; the delete was purged with its original.
    revocation, purge = loop.decisions.entries(since=loop.decisions.cursor - 2)
    assert revocation.decision.action is DiffAction.ACL_ONLY
    assert (purge.decision.action, purge.purged) == (DiffAction.PURGE, 1)
    assert changelog.rawstore.versions("hr_api:employees/E-1012") == []


def test_t3_parses_what_t2_processes_and_forgets_what_it_purges(
    clients: tuple[TestClient, TestClient, TestClient], tmp_path: Path
) -> None:
    docs, hr, idp = clients
    create_database(tmp_path / "hr.sqlite")
    changelog = ChangeLog.in_memory()
    connectors = build_connectors(docs, hr, idp, tmp_path / "hr.sqlite", changelog)
    loop = SyncLoop(connectors, changelog, EventLog(), listing_every=1)
    loop.run_round()
    parsed = loop.parsed
    assert len(parsed.entries()) == 12  # every page; nothing from the other sources
    assert {e.document.document_key.partition(":")[0] for e in parsed.entries()} == {"hr_docs"}
    assert parsed.records() == {"hr_api": 17, "hr_db": 16, "identity": 12}  # D7: not parsed

    page = docs.get("/pages/HR-PL-01").json()
    docs.put("/pages/HR-PL-01", json={"body": page["body"]})  # same text: T2 skips
    docs.put("/pages/HR-PL-04", json={"body": "# Remote work\n\nFriday too.\n"})
    docs.delete("/pages/HR-PL-99")
    loop.run_round()  # the full listing finds all three

    resaved, edited = parsed.get("hr_docs:HR-PL-01"), parsed.get("hr_docs:HR-PL-04")
    assert resaved is not None
    assert edited is not None
    assert (resaved.runs, resaved.document.version) == (1, "1.1")  # not parsed again
    assert (edited.runs, edited.document.version) == (2, "2.1")
    assert [b.text for b in edited.document.blocks] == ["Remote work", "Friday too."]
    assert parsed.get("hr_docs:HR-PL-99") is None  # deleted: its parsed text is gone
    assert len(parsed.entries()) == 11


def test_a_failing_source_does_not_stop_the_others(
    clients: tuple[TestClient, TestClient, TestClient], tmp_path: Path
) -> None:
    docs, hr, idp = clients
    changelog = ChangeLog.in_memory()  # no database file: the CDC connector fails every round
    connectors = build_connectors(docs, hr, idp, tmp_path / "missing.sqlite", changelog)
    loop = SyncLoop(connectors, changelog, EventLog())
    assert loop.run_round() == 12 + 17 + 12
    assert any("hr_db" in error for error in loop.errors)


def test_the_loop_runs_in_the_background(
    clients: tuple[TestClient, TestClient, TestClient], tmp_path: Path
) -> None:
    docs, hr, idp = clients
    create_database(tmp_path / "hr.sqlite")
    changelog = ChangeLog.in_memory()
    connectors = build_connectors(docs, hr, idp, tmp_path / "hr.sqlite", changelog)
    loop = SyncLoop(connectors, changelog, EventLog())
    with loop.running(interval_s=0.01):
        while loop.rounds < 2:
            pass
    assert loop.errors == []


def test_the_command_refuses_outside_the_local_profile() -> None:
    result = CliRunner().invoke(app, ["inspector"])  # tests run with EKB_PROFILE=ci
    assert result.exit_code == 2
    assert "only in the 'local' profile" in result.output


def test_the_command_exports_the_schema(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"
    result = CliRunner().invoke(app, ["inspector", "--export-openapi", str(target)])
    assert result.exit_code == 0, result.output
    assert '"title": "ekb inspector"' in target.read_text()


@pytest.mark.parametrize(
    ("option", "value"),
    [("--listing-every", "0"), ("--interval", "0"), ("--interval", "-1"), ("--interval", "nan")],
)
def test_invalid_sync_schedule_is_rejected(option: str, value: str) -> None:
    result = CliRunner().invoke(app, ["inspector", option, value])
    assert result.exit_code == 2


def test_a_failing_consumer_is_retried_next_round_and_loses_nothing(
    clients: tuple[TestClient, TestClient, TestClient],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs, hr, idp = clients
    create_database(tmp_path / "hr.sqlite")
    log, changelog = EventLog(), ChangeLog.in_memory()
    connectors = build_connectors(docs, hr, idp, tmp_path / "hr.sqlite", changelog)
    loop = SyncLoop(connectors, changelog, log)

    def broken(_: object) -> None:
        raise ValueError("the inspector's copy failed")

    monkeypatch.setattr(log, "append", broken)
    loop.run_round()
    assert any("consumer group inspector" in error for error in loop.errors)
    assert log.cursor == 0
    monkeypatch.undo()
    loop.run_round()
    assert log.cursor == 57  # every event was still pending, none was skipped


def test_a_failing_t2_round_records_nothing_and_is_retried_in_full(
    clients: tuple[TestClient, TestClient, TestClient],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If T2 can't hand a decision on, it records nothing, so the retry is not mistaken
    for a repeat and skipped."""
    docs, hr, idp = clients
    create_database(tmp_path / "hr.sqlite")
    changelog = ChangeLog.in_memory()
    connectors = build_connectors(docs, hr, idp, tmp_path / "hr.sqlite", changelog)
    loop = SyncLoop(connectors, changelog, EventLog())

    def broken(decision: object, purged: int = 0) -> None:
        raise ValueError("the decision log failed")

    monkeypatch.setattr(loop.decisions, "append", broken)
    loop.run_round()
    assert any("consumer group t2_diff" in error for error in loop.errors)
    assert changelog.registry.records() == []
    monkeypatch.undo()
    loop.run_round()
    counts = loop.decisions.counts()
    assert counts[DiffAction.PROCESS] == 57  # every item processed, none lost
    # With nothing recorded, the HR listing reported the 17 records the delta had
    # already sent; T2 skipped those copies as repeats.
    assert counts[DiffAction.SKIP] == 17


@pytest.mark.parametrize(
    ("source", "item", "listing_index"),
    [
        ("hr_docs", "HR-PL-04", 0),
        ("hr_api", "employees/E-1001", 1),
    ],
)
def test_listing_repairs_missing_original_and_tombstones_absent_items(
    clients: tuple[TestClient, TestClient, TestClient],
    tmp_path: Path,
    source: str,
    item: str,
    listing_index: int,
) -> None:
    changelog = ChangeLog.in_memory()
    listing = build_connectors(*clients, tmp_path / "unused.sqlite", changelog).listings[
        listing_index
    ]
    listing.sync()
    changelog.record_processed()
    key = f"{source}:{item}"
    before = changelog.registry.get(key)
    assert before is not None
    assert before.raw_pointer is not None
    for source_id in (item, "vanished"):
        changelog.bus.publish(
            acl_change(source_id, before.version, Acl(), source, content_hash="mismatch")
        )
    changelog.record_processed()
    pending = changelog.registry.get(key)
    assert pending is not None
    assert pending.acl == Acl()
    assert pending.raw_pointer is None
    assert changelog.registry.known(source)[item] == NeedsOriginal(version=before.version)
    assert listing.sync() == 2
    changelog.record_processed()
    repaired = changelog.registry.get(key)
    gone = changelog.registry.get(f"{source}:vanished")
    assert repaired is not None
    assert repaired.raw_pointer == before.raw_pointer
    assert repaired.content_hash == before.content_hash
    assert gone is not None
    assert gone.deleted
    assert gone.version == before.version
    assert listing.sync() == 0
