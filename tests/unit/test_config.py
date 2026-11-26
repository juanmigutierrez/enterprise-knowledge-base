from pathlib import Path

import pytest

from ekb.platform.config import load_settings


def test_ci_profile_overrides_default() -> None:
    settings = load_settings()
    assert settings.profile == "ci"
    assert settings.gateway.tasks["context_header"] == ("fake:fake-small", "fake:fake-large")
    assert settings.steps["t7_embed"].option == "fake"
    # untouched steps keep the default choice
    assert settings.steps["t2_diff"].option == "content_hash"
    # boxes 3 and 5 stay in memory in CI, even with --docker (S17)
    assert settings.steps["search"].option == "in_memory"


def test_unknown_profile_fails_loudly(tmp_path: Path) -> None:
    (tmp_path / "default.yaml").write_text("steps: {}\n")
    with pytest.raises(FileNotFoundError, match="unknown profile"):
        load_settings(config_dir=tmp_path, profile="nope")


def test_a_profile_that_picks_another_option_does_not_inherit_its_params(tmp_path: Path) -> None:
    (tmp_path / "profiles").mkdir()
    (tmp_path / "default.yaml").write_text(
        "steps:\n  t4_chunk: { option: structure_aware, params: { max_tokens: 400 } }\n"
        "gateway:\n  cache: { option: sqlite, params: { path: x.sqlite } }\n",
        encoding="utf-8",
    )
    (tmp_path / "profiles" / "p.yaml").write_text(
        "steps:\n  t4_chunk: { params: { max_tokens: 200 } }\n"
        "gateway:\n  cache: { option: in_memory }\n",
        encoding="utf-8",
    )

    settings = load_settings(config_dir=tmp_path, profile="p")

    assert settings.steps["t4_chunk"].option == "structure_aware"  # same option: params merge
    assert settings.steps["t4_chunk"].params == {"max_tokens": 200}
    assert settings.gateway.cache.option == "in_memory"
    assert settings.gateway.cache.params == {}  # another option: replaced whole
