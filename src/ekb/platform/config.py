"""Runtime configuration: which option each step uses, and its parameters.

Resolution order (later wins): ``config/default.yaml`` -> ``config/profiles/<profile>.yaml``.
The profile comes from ``EKB_PROFILE`` (``local`` if unset). Secrets never live
in these files; they come from the environment (``.env`` locally, IAM on AWS).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

Provider = Literal["fake", "ollama", "bedrock"]


class StepChoice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    option: str
    params: dict[str, Any] = Field(default_factory=dict)
    card: str | None = None  # option card in docs/option-evaluation.md


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: str
    provider: Provider
    steps: dict[str, StepChoice]


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping at the top level")
    return data


def load_settings(config_dir: Path | None = None, profile: str | None = None) -> Settings:
    config_dir = config_dir or Path(os.environ.get("EKB_CONFIG_DIR", "config"))
    profile = profile or os.environ.get("EKB_PROFILE", "local")

    data = _read_yaml(config_dir / "default.yaml")
    profile_file = config_dir / "profiles" / f"{profile}.yaml"
    if profile != "local":
        if not profile_file.exists():
            raise FileNotFoundError(f"unknown profile {profile!r}: {profile_file} not found")
        data = _deep_merge(data, _read_yaml(profile_file))

    return Settings(profile=profile, **data)
