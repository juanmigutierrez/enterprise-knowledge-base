"""``ekb demo gateway`` tells the story it promises, offline, and says how to fix a missing key."""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ekb.app.cli import app
from ekb.app.demos.gateway import (
    FAKE_CONFIG,
    HEADER,
    DemoSetupError,
    Story,
    _console_echo,
    run_gateway_demo,
)
from ekb.app.gateway import FreeRequests
from ekb.platform.config import load_settings
from ekb.platform.gateway.config import GatewayConfig
from ekb.platform.gateway.options.log_jsonl import JsonlCostLog
from ekb.platform.gateway.options.log_memory import InMemoryCostLog
from ekb.platform.gateway.port import CostLog
from tests.conftest import REPO_ROOT
from tests.unit.platform.gateway.helpers import FakeClock

KEYS = ("GOOGLE_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY", "OPENROUTER_API_KEY")


def test_console_handles_provider_unicode_on_a_legacy_pipe(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", write_through=True)
    monkeypatch.setattr(sys, "stdout", stream)
    _console_echo("31\u202fMarch")
    assert raw.getvalue().strip() == b"31\\u202fMarch"


def run(
    workdir: Path,
    *,
    config: GatewayConfig = FAKE_CONFIG,
    log: CostLog | None = None,
    environ: dict[str, str] | None = None,
    counter: FreeRequests | None = None,
) -> tuple[str, Story]:
    lines: list[str] = []
    workdir.mkdir(parents=True, exist_ok=True)
    story = run_gateway_demo(
        config,
        environ=environ or {},
        workdir=workdir,
        log=log if log is not None else InMemoryCostLog(),
        echo=lines.append,
        clock=FakeClock(),
        openrouter=lambda _key: counter,
    )
    return "\n".join(lines), story


def test_the_story_misses_reuses_changes_routes_fails_over_and_restarts(tmp_path: Path) -> None:
    output, story = run(tmp_path / "run")
    replies = story.replies

    assert not replies["first"].cached
    assert replies["again"].cached
    assert replies["again"].text == replies["first"].text
    assert replies["version"].key != replies["first"].key
    assert not replies["changed"].cached
    assert replies["answer"].candidate == "fake:fake-large"
    assert story.failover == "fake:fake-large"
    assert replies["failover"].steps[0].result.startswith("skipped: daily quota fake_small")
    assert replies["restart"].cached  # a new gateway on the same cache file
    assert "7 records this run, 5 requests, 2 hits." in output
    assert output.count("from the cache, no request") == 2


def test_the_story_is_the_same_every_time(tmp_path: Path) -> None:
    first, _ = run(tmp_path / "a")
    second, _ = run(tmp_path / "b")

    def stable(text: str) -> str:
        return re.sub(r"\d+ ms", "N ms", text)

    assert stable(first) == stable(second)


def test_requests_count_against_the_kept_log_across_runs(tmp_path: Path) -> None:
    log = JsonlCostLog(str(tmp_path / "kept" / "model-calls.jsonl"))

    run(tmp_path / "a", log=log)
    output, _ = run(tmp_path / "b", log=log)

    assert len(log.records()) == 14
    assert "fake_small           3/100 today" in output  # before the second run
    assert "fake_small           6/100 today" in output  # after it
    assert "Kept in" in output
    assert "Model gateway" in output


def test_with_one_model_the_failover_explains_why_the_call_fails(tmp_path: Path) -> None:
    single = FAKE_CONFIG.model_copy(
        update={"tasks": {**FAKE_CONFIG.tasks, HEADER: ("fake:fake-small",)}}
    )

    output, story = run(tmp_path / "run", config=single)

    assert "no model available for 'context_header'" in story.failover
    assert "the call fails, saying why" in output


def test_openrouters_own_counter_is_shown_when_its_key_is_set(tmp_path: Path) -> None:
    environ = {"OPENROUTER_API_KEY": "or-test-only"}

    shown, _ = run(
        tmp_path / "a", environ=environ, counter=FreeRequests(used=3, limit=50, remaining=47)
    )
    unread, _ = run(tmp_path / "b", environ=environ, counter=None)

    assert "3/50 free requests today, 47 left" in shown
    assert "OpenRouter's own counter: could not be read." in unread
    assert "or-test-only" not in shown


def test_without_any_key_it_says_which_keys_and_how(tmp_path: Path) -> None:
    local = load_settings(config_dir=REPO_ROOT / "config", profile="local").gateway

    with pytest.raises(DemoSetupError, match="GROQ_API_KEY") as raised:
        run(tmp_path / "run", config=local)

    assert "--env-file .env" in str(raised.value)
    assert "--fake" in str(raised.value)


def test_the_ci_profile_runs_the_story_through_the_configured_gateway(tmp_path: Path) -> None:
    output, story = run(tmp_path / "run", config=load_settings().gateway)

    assert story.replies["first"].candidate == "fake:fake-small"
    assert "context_header  fake:fake-small -> fake:fake-large" in output


def test_the_command_runs_offline_with_fake() -> None:
    result = CliRunner().invoke(app, ["demo", "gateway", "--fake"])

    assert result.exit_code == 0, result.output
    assert "10. The cost log: 7 records this run, 5 requests, 2 hits." in result.output
    assert "thrown away" in result.output


def test_the_command_fails_clearly_without_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EKB_PROFILE", "local")
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)

    result = CliRunner().invoke(app, ["demo", "gateway"])

    assert result.exit_code == 1
    assert "no model provider has a key" in result.output
