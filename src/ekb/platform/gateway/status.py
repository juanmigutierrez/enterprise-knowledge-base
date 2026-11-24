"""Which providers can be called right now: is each one's key set? Never the key itself.

Shared by ``ekb options``, the demo and the inspector's gateway panel, so they all
answer the same way, and none of them ever holds a key's value.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from ekb.platform.gateway.config import GatewayConfig, split_ref


class ProviderStatus(BaseModel):
    """A provider some task uses: its key's variable, whether it is set, its models."""

    model_config = ConfigDict(frozen=True)

    provider: str
    key_env: str | None
    ready: bool
    models: list[str]


def provider_status(
    config: GatewayConfig, environ: Mapping[str, str] | None = None
) -> list[ProviderStatus]:
    """For each provider some task routes to, in first-seen order."""
    env = os.environ if environ is None else environ
    used: dict[str, list[str]] = {}
    for ref in config.used_models():
        used.setdefault(split_ref(ref)[0], []).append(ref)
    statuses: list[ProviderStatus] = []
    for name, refs in used.items():
        key_env = config.providers[name].key_env
        ready = key_env is None or bool(env.get(key_env))
        statuses.append(ProviderStatus(provider=name, key_env=key_env, ready=ready, models=refs))
    return statuses
