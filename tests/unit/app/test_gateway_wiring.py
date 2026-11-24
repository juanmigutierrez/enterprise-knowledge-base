"""The gateway wired from the settings: clients, keys from the environment, the spend cap."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from ekb.app.cli import app
from ekb.app.gateway import (
    build_cache,
    build_cost_log,
    build_gateway,
    max_spend,
    openrouter_free_requests,
    provider_status,
)
from ekb.platform.config import load_settings
from ekb.platform.gateway.config import GatewayConfig, OptionChoice
from ekb.platform.gateway.errors import NoModelAvailableError
from ekb.platform.gateway.options.cache_memory import InMemoryCache
from ekb.platform.gateway.options.cache_sqlite import SqliteCache
from ekb.platform.gateway.options.fake import FakeClient
from ekb.platform.gateway.options.langchain import LangChainClient
from ekb.platform.gateway.options.log_jsonl import JsonlCostLog
from ekb.platform.gateway.options.log_memory import InMemoryCostLog
from tests.conftest import REPO_ROOT

KEYS = {"GROQ_API_KEY": "gsk-test-only", "OPENROUTER_API_KEY": "or-test-only"}


def local_config() -> GatewayConfig:
    return load_settings(config_dir=REPO_ROOT / "config", profile="local").gateway


def test_the_ci_gateway_answers_every_task_offline() -> None:
    gateway = build_gateway(load_settings().gateway, environ={})

    for task in gateway.tasks:
        reply = gateway.complete(
            task, "Where is the leave policy?", prompt_version="t@1", max_output_tokens=20
        )
        assert reply.candidate == "fake:fake-small"
    assert isinstance(gateway.cache, InMemoryCache)
    assert isinstance(gateway.log, InMemoryCostLog)
    assert isinstance(gateway.client("fake:fake-large"), FakeClient)


def test_the_local_gateway_builds_a_langchain_client_per_model_with_its_key() -> None:
    gateway = build_gateway(
        local_config(), environ=KEYS, cache=InMemoryCache(), log=InMemoryCostLog()
    )

    groq_client = gateway.client("groq:openai/gpt-oss-20b")
    google_client = gateway.client("google_genai:gemini-3.5-flash-lite")
    assert isinstance(groq_client, LangChainClient)
    assert groq_client.check() is None
    assert google_client is not None
    assert google_client.check() == "GOOGLE_API_KEY is not set"


def test_without_any_key_every_real_model_is_skipped_and_nothing_is_sent() -> None:
    gateway = build_gateway(
        local_config(), environ={}, cache=InMemoryCache(), log=InMemoryCostLog()
    )

    with pytest.raises(
        NoModelAvailableError, match="no model available for 'context_header'"
    ) as raised:
        gateway.complete("context_header", "x", prompt_version="t@1", max_output_tokens=5)

    assert "GOOGLE_API_KEY is not set" in str(raised.value)
    assert gateway.log.records() == []


def test_the_cache_and_log_options_build_where_configured(tmp_path: Path) -> None:
    cache = build_cache(
        OptionChoice(option="sqlite", params={"path": str(tmp_path / "c" / "r.sqlite")})
    )
    log = build_cost_log(OptionChoice(option="jsonl", params={"path": str(tmp_path / "l.jsonl")}))

    assert isinstance(cache, SqliteCache)
    cache.close()
    assert (tmp_path / "c" / "r.sqlite").exists()
    assert isinstance(log, JsonlCostLog)


def test_the_spend_cap_comes_from_the_environment_when_set() -> None:
    config = local_config()

    for value in ("nan", "inf", "-inf"):
        with pytest.raises(ValueError, match="must be finite"):
            max_spend(config, {"EKB_MAX_SPEND_USD": value})
    assert max_spend(config, {}) == 25
    assert max_spend(config, {"EKB_MAX_SPEND_USD": "2.5"}) == 2.5
    with pytest.raises(ValueError, match="must be a number"):
        max_spend(config, {"EKB_MAX_SPEND_USD": "lots"})
    with pytest.raises(ValueError, match="must not be negative"):
        max_spend(config, {"EKB_MAX_SPEND_USD": "-1"})


def test_provider_status_says_whether_a_key_is_set_never_what_it_is() -> None:
    statuses = {s.provider: s for s in provider_status(local_config(), KEYS)}

    assert set(statuses) == {"google_genai", "groq", "mistralai", "openrouter"}  # ollama: no task
    assert statuses["groq"].ready
    assert not statuses["google_genai"].ready
    dumped = json.dumps([s.model_dump() for s in statuses.values()])
    assert "gsk-test-only" not in dumped
    assert "or-test-only" not in dumped


def test_openrouter_free_requests_reads_the_accounts_daily_counter() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = {"data": {"free_model_daily_requests": {"used": 12, "limit": 50, "remaining": 38}}}
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        counter = openrouter_free_requests("or-test-only", http)

    assert counter is not None
    assert (counter.used, counter.limit, counter.remaining) == (12, 50, 38)
    assert seen[0].headers["Authorization"] == "Bearer or-test-only"


@pytest.mark.parametrize(
    "reply",
    [httpx.Response(401, json={"error": "bad key"}), httpx.Response(200, json={"data": {}})],
)
def test_openrouter_free_requests_is_none_when_it_cannot_be_read(reply: httpx.Response) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: reply)) as http:
        assert openrouter_free_requests("k", http) is None


def test_ekb_options_shows_the_gateway() -> None:
    result = CliRunner().invoke(app, ["options"])

    assert result.exit_code == 0
    assert "model gateway (S7): cache=in_memory cost_log=in_memory" in result.output
    assert "task context_header" in result.output
    assert "fake:fake-small -> fake:fake-large" in result.output
