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

from ekb.app.cli import app
from ekb.app.demos.s1 import run_s1
from ekb.app.demos.s1b import run_s1b
from ekb.app.demos.s1c import run_s1c
from ekb.app.demos.s1d import run_s1d
from tests.conftest import REPO_ROOT

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

        run_s1c(client, echo=echo)
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
    ],
)
def test_cli_runs_each_demo_end_to_end(
    step: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    result = CliRunner().invoke(app, ["demo", step])
    assert result.exit_code == 0, result.output
    assert expected in result.output


@pytest.mark.parametrize("step", ["s1", "s1b", "s1c"])
def test_cli_reports_an_unreachable_mock_cleanly(step: str) -> None:
    result = CliRunner().invoke(app, ["demo", step, "--url", "http://127.0.0.1:9"])
    assert result.exit_code == 1
    assert "Demo failed" in result.output


def test_cli_s1d_reports_a_failed_start(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # sources/ is not here
    result = CliRunner().invoke(app, ["demo", "s1d"])
    assert result.exit_code == 1
    assert "repository root" in result.output
