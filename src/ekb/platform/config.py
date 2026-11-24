"""Runtime configuration: which option each step uses, its parameters, and the model gateway.

Resolution order (later wins): ``config/default.yaml`` -> ``config/profiles/<profile>.yaml``.
The profile comes from ``EKB_PROFILE`` (``local`` if unset). Secrets never live
in these files; they come from the environment (``.env`` locally, IAM on AWS):
the ``gateway`` section only names the variables that hold the API keys.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ekb.platform.gateway.config import GatewayConfig


class StepChoice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    option: str
    params: dict[str, Any] = Field(default_factory=dict)
    card: str | None = None  # option card in docs/option-evaluation.md


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: str
    steps: dict[str, StepChoice]
    gateway: GatewayConfig = GatewayConfig()
    """Which models each task may use, their quotas and prices (S7, D15)."""


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Mappings merge key by key, except a choice that names another ``option``:
    that one is replaced whole, because another option's params mean nothing to it
    (the ``ci`` profile's in-memory cache must not inherit the SQLite ``path``)."""
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if (
            isinstance(value, dict)
            and isinstance(current, dict)
            and not _other_option(current, value)
        ):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def _other_option(base: dict[str, Any], override: dict[str, Any]) -> bool:
    return "option" in override and override["option"] != base.get("option")


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
