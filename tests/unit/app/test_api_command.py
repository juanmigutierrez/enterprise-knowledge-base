"""``ekb api`` (S15): builds the system, prints the tokens and the connect command, serves MCP.

The mocks run in-process and ``uvicorn.run`` is replaced, so the command's whole
startup runs without a port or a subprocess; the sync loop is tested on its own.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sources.mock_docs.app import create_app as docs_app
from sources.mock_hr_api.app import create_app as hr_api_app
from sources.mock_hr_db.db import create_database
from sources.mock_idp.app import create_app as idp_app
from starlette.applications import Starlette
from typer.testing import CliRunner

from ekb.access.auth.options.static_tokens import StaticTokens
from ekb.app import api as api_module
from ekb.app.choices import choose, choose_assistant, choose_query
from ekb.app.cli import app
from ekb.app.system import KnowledgeSystem, syncing
from ekb.platform.config import load_settings
from tests.http import as_httpx

pytestmark = pytest.mark.filterwarnings(
    "ignore:You should not use the 'timeout' argument with the TestClient"
)
DANA = {"X-Actor": "dana"}


def test_the_command_starts_the_system_prints_the_tokens_and_serves_on_localhost(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # the token file goes to .cache/ekb/ under the working folder
    monkeypatch.setenv("EKB_PROFILE", "local")
    apps: dict[str, Callable[[], FastAPI]] = {
        "sources.mock_docs": docs_app,
        "sources.mock_hr_api": hr_api_app,
        "sources.mock_idp": idp_app,
    }

    @contextlib.contextmanager
    def in_process(module: str, url: str | None = None) -> Iterator[httpx.Client]:
        with TestClient(apps[module]()) as client:
            yield as_httpx(client)

    def database(module: str, args: list[str], **kwargs: Any) -> str:
        create_database(Path(args[-1]))
        return ""

    served: dict[str, Any] = {}

    def run(asgi: Starlette, **kwargs: Any) -> None:
        served.update(kwargs, app=asgi)

    monkeypatch.setattr(api_module, "mock_client", in_process)
    monkeypatch.setattr(api_module, "run_mock_command", database)
    monkeypatch.setattr(uvicorn, "run", run)

    result = CliRunner().invoke(app, ["api", "--port", "18080"])

    assert result.exit_code == 0, result.output
    assert served["host"] == "127.0.0.1"
    assert served["port"] == 18080
    assert isinstance(served["app"], Starlette)
    output = result.output
    assert (
        "MCP (Streamable HTTP): http://127.0.0.1:18080/mcp   tools: whoami, search, ask" in output
    )
    assert "auth static_tokens: static_tokens@1(hr-assistant, 5 users)" in output
    assert "answer model -> extractive (offline: no model): extractive@1(quotes=3)" in output
    assert "Loaded 48 searchable chunks" in output
    tokens = (tmp_path / ".cache" / "ekb" / "test-tokens.json").read_text()
    alice = next(line.split()[2] for line in output.splitlines() if line.startswith("  alice "))
    assert alice in tokens
    assert f'--header "Authorization: Bearer {alice}"' in output


def test_the_command_refuses_a_bad_interval_and_reports_a_failed_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = CliRunner().invoke(app, ["api", "--interval", "0"])
    assert result.exit_code == 2
    monkeypatch.chdir(tmp_path)  # no sources/ here: the database mock cannot start
    dead = "http://127.0.0.1:9"
    args = ["api", "--docs-url", dead, "--hr-url", dead, "--idp-url", dead]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert "The Knowledge API could not start" in result.output


def test_the_sync_loop_picks_up_a_change_and_survives_a_failed_round(tmp_path: Path) -> None:
    create_database(tmp_path / "hr.sqlite")
    settings = load_settings(profile="local")
    with (
        TestClient(docs_app(post=lambda url, payload: None)) as docs,
        TestClient(hr_api_app()) as hr,
        TestClient(idp_app()) as idp,
    ):
        system = KnowledgeSystem(
            as_httpx(docs),
            as_httpx(hr),
            as_httpx(idp),
            tmp_path / "hr.sqlite",
            write=choose(settings, offline=True),
            query=choose_query(settings),
            assistant=choose_assistant(settings, offline=True),
            tokens=StaticTokens(issued=()),
        )
        system.sync(listings=True)
        assert "hr_docs:HR-PL-99" in system.stores.search.documents()
        docs.delete("/pages/HR-PL-99", headers=DANA)
        failures = {"left": 1}
        original = system.sync

        def flaky(*, listings: bool = False) -> None:
            if failures["left"]:
                failures["left"] -= 1
                raise httpx.ConnectError("the source is down")
            original(listings=listings)

        system.sync = flaky  # type: ignore[method-assign]
        with syncing(system, interval_s=0.05, listing_every=1):
            deadline = time.monotonic() + 30
            while "hr_docs:HR-PL-99" in system.stores.search.documents():
                assert time.monotonic() < deadline, "the loop never purged the deleted page"
                time.sleep(0.05)
        assert failures["left"] == 0
