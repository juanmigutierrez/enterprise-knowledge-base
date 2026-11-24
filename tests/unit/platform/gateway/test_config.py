"""The ``gateway:`` settings: references are checked when they load, not on the first call."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ekb.platform.config import load_settings
from ekb.platform.gateway.config import GatewayConfig, Price, split_ref
from tests.conftest import REPO_ROOT
from tests.unit.platform.gateway.helpers import make_config

D15_TASKS = {
    "context_header",
    "classify",
    "entities",
    "entities_hard",
    "plan_query",
    "answer",
    "judge",
}


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("groq:openai/gpt-oss-20b", ("groq", "openai/gpt-oss-20b")),
        ("ollama:gemma4:e4b", ("ollama", "gemma4:e4b")),  # only the first colon separates
        ("openrouter:openrouter/free", ("openrouter", "openrouter/free")),
    ],
)
def test_a_model_is_named_provider_colon_model(ref: str, expected: tuple[str, str]) -> None:
    assert split_ref(ref) == expected


@pytest.mark.parametrize("ref", ["gemini", ":gemini", "google_genai:"])
def test_a_model_name_without_both_halves_is_refused(ref: str) -> None:
    with pytest.raises(ValueError, match="provider:model"):
        split_ref(ref)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"tasks": {"header": ("fake:nope",)}}, "model 'fake:nope' is not under models"),
        ({"tasks": {"header": ()}}, "has no candidates"),
        ({"tasks": {"header": ("fake:one", "fake:one")}}, "lists a candidate twice"),
        ({"models": {"cloud:x": {}}}, "provider 'cloud' is not under providers"),
        ({"models": {"fake:one": {"quota": "missing_q"}}}, "quota 'missing_q' is not under quotas"),
        ({"quotas": {"one_q": {"resets": "Europe/Atlantis"}}}, "unknown time zone"),
        ({"models": {"fake:one": {"price": {"input": -1}}}}, "greater than or equal to 0"),
        ({"providers": {"groq": {}}, "models": {"groq:test": {}}}, "explicit price"),
        ({"max_spend_usd": float("inf")}, "finite"),
        ({"surprise": 1}, "Extra inputs are not permitted"),
    ],
)
def test_a_broken_reference_fails_when_the_settings_load(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        make_config(**overrides)


def test_price_is_per_million_tokens() -> None:
    price = Price(input=0.10, output=0.50)  # Claude Haiku 5.5 (D15)

    assert price.cost(1_000_000, 0) == pytest.approx(0.10)
    assert price.cost(2_000, 400) == pytest.approx(0.0004)
    assert not price.free
    assert Price().free


def _gateway(profile: str) -> GatewayConfig:
    return load_settings(config_dir=REPO_ROOT / "config", profile=profile).gateway


def test_the_local_profile_routes_every_d15_task_across_several_free_providers() -> None:
    config = _gateway("local")

    assert set(config.tasks) == D15_TASKS
    for task, chain in config.tasks.items():
        providers = {split_ref(ref)[0] for ref in chain}
        assert len(providers) >= 3, f"{task} has no real fallback"
        assert all(config.models[ref].price.free for ref in chain), f"{task} would spend money"
        assert all(config.models[ref].quota for ref in chain), f"{task} has an unguarded model"
    assert config.cache.option == "sqlite"
    assert config.cost_log.option == "jsonl"


def test_the_local_profile_keeps_secrets_out_naming_only_their_variables() -> None:
    config = _gateway("local")
    text = (REPO_ROOT / "config" / "default.yaml").read_text(encoding="utf-8")

    assert {p.key_env for p in config.providers.values() if p.key_env} == {
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "MISTRAL_API_KEY",
        "OPENROUTER_API_KEY",
    }
    for key_prefix in ("sk-", "gsk_", "AIza"):  # OpenRouter, Groq, Google
        assert key_prefix not in text


def test_a_paid_model_must_state_its_price() -> None:
    config = _gateway("local")

    assert not config.models["openrouter:anthropic/claude-haiku-5.5"].price.free


def test_the_ci_profile_never_calls_a_real_model() -> None:
    config = _gateway("ci")

    assert set(config.tasks) == D15_TASKS
    assert {split_ref(ref)[0] for ref in config.used_models()} == {"fake"}
    assert config.cache.option == "in_memory"
    assert config.cost_log.option == "in_memory"


def test_the_bedrock_profile_is_the_production_design() -> None:
    config = _gateway("bedrock")

    assert {split_ref(ref)[0] for ref in config.used_models()} == {"bedrock_converse"}
    assert config.tasks["context_header"] == ("bedrock_converse:eu.anthropic.claude-haiku-5-5",)
    assert all(not config.models[ref].price.free for ref in config.used_models())


def test_a_settings_file_without_a_gateway_gets_an_empty_one(tmp_path: Path) -> None:
    (tmp_path / "default.yaml").write_text("steps: {}\n", encoding="utf-8")

    settings = load_settings(config_dir=tmp_path, profile="local")

    assert settings.gateway.tasks == {}
