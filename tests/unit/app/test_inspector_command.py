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

from ekb.app.cli import app
from ekb.app.inspector import SyncLoop, build_connectors
from ekb.contracts import ChangeOp
from ekb.operate.inspector.log import EventLog


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
    log = EventLog()
    loop = SyncLoop(build_connectors(docs, hr, idp, tmp_path / "hr.sqlite", log), listing_every=2)
    assert loop.run_round() == 12 + 17 + 16 + 12  # pages, HR records, DB rows, users
    assert {s: len(log.known(s)) for s in ("hr_docs", "hr_api", "hr_db", "identity")} == {
        "hr_docs": 12,
        "hr_api": 17,
        "hr_db": 16,
        "identity": 12,
    }
    assert loop.run_round() == 0  # nothing changed

    hr.delete("/employees/E-1012")
    assert loop.run_round() == 2  # leave ACL revocation, then the HR listing delete
    revoked = log.entries(since=log.cursor - 2)[0].event
    assert revoked.op is ChangeOp.ACL_CHANGE
    assert revoked.acl.users == frozenset()
    (last,) = log.entries(since=log.cursor - 1)
    assert (last.event.op, last.event.source_id) == (ChangeOp.DELETE, "employees/E-1012")


def test_a_failing_source_does_not_stop_the_others(
    clients: tuple[TestClient, TestClient, TestClient], tmp_path: Path
) -> None:
    docs, hr, idp = clients
    log = EventLog()  # no database file: the CDC connector fails every round
    loop = SyncLoop(build_connectors(docs, hr, idp, tmp_path / "missing.sqlite", log))
    assert loop.run_round() == 12 + 17 + 12
    assert any("hr_db" in error for error in loop.errors)


def test_the_loop_runs_in_the_background(
    clients: tuple[TestClient, TestClient, TestClient], tmp_path: Path
) -> None:
    docs, hr, idp = clients
    create_database(tmp_path / "hr.sqlite")
    loop = SyncLoop(build_connectors(docs, hr, idp, tmp_path / "hr.sqlite", EventLog()))
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
