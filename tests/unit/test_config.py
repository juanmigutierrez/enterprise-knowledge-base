from pathlib import Path

import pytest

from ekb.platform.config import load_settings


def test_ci_profile_overrides_default() -> None:
    settings = load_settings()
    assert settings.profile == "ci"
    assert settings.provider == "fake"
    assert settings.steps["t7_embed"].option == "fake"
    # untouched steps keep the default choice
    assert settings.steps["search"].option == "opensearch"


def test_unknown_profile_fails_loudly(tmp_path: Path) -> None:
    (tmp_path / "default.yaml").write_text("provider: fake\nsteps: {}\n")
    with pytest.raises(FileNotFoundError, match="unknown profile"):
        load_settings(config_dir=tmp_path, profile="nope")
