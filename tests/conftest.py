"""Shared fixtures. Tests run against the CI profile: no models, no network."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _ci_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EKB_CONFIG_DIR", str(REPO_ROOT / "config"))
    monkeypatch.setenv("EKB_PROFILE", "ci")
