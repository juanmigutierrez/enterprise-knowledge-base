"""The model gateway wired from the settings (S7). The only place its options are chosen.

``build_gateway(settings.gateway)`` builds:

- one **client** per model a task may use: ``fake:*`` models get the offline
  :class:`FakeClient`, every other provider a :class:`LangChainClient` with its
  API key read from the environment variable the configuration names. An
  embedding model (S10) gets :class:`FakeEmbeddingClient` or
  :class:`LangChainEmbeddingClient` the same way;
- the **cache** and the **cost log** the configuration chooses (``sqlite`` and
  ``jsonl`` locally, ``in_memory`` in the ``ci`` profile);
- the **spend cap**, from ``EKB_MAX_SPEND_USD`` when set.

Keys are read here and handed to the clients, never written anywhere: not to the
log, not to the cache, not to the inspector, which only learns whether each is set.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict

from ekb.platform import registry
from ekb.platform.gateway.config import GatewayConfig, OptionChoice, split_ref
from ekb.platform.gateway.gateway import ModelGateway, utc_now
from ekb.platform.gateway.options.cache_memory import InMemoryCache
from ekb.platform.gateway.options.cache_sqlite import SqliteCache
from ekb.platform.gateway.options.fake import FakeClient, FakeEmbeddingClient
from ekb.platform.gateway.options.langchain import LangChainClient, LangChainEmbeddingClient
from ekb.platform.gateway.options.log_jsonl import JsonlCostLog
from ekb.platform.gateway.options.log_memory import InMemoryCostLog
from ekb.platform.gateway.port import CostLog, EmbeddingClient, ModelClient, ResponseCache
from ekb.platform.gateway.status import ProviderStatus, provider_status

OPTIONS = (
    FakeClient,
    LangChainClient,
    FakeEmbeddingClient,
    LangChainEmbeddingClient,
    InMemoryCache,
    SqliteCache,
    InMemoryCostLog,
    JsonlCostLog,
)
"""Imported, so every gateway option is registered."""

__all__ = ["ProviderStatus", "build_gateway", "provider_status"]

MAX_SPEND_ENV = "EKB_MAX_SPEND_USD"
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"

Environ = Mapping[str, str]


def build_client(config: GatewayConfig, ref: str, environ: Environ) -> ModelClient:
    """The client for one ``provider:model``."""
    provider, model = split_ref(ref)
    if provider == "fake":
        built = registry.create("model_client", "fake", model=model)
    else:
        spec = config.providers[provider]
        built = registry.create(
            "model_client",
            "langchain",
            provider=provider,
            model=model,
            api_key=environ.get(spec.key_env) if spec.key_env else None,
            key_env=spec.key_env,
            params=config.models[ref].params,
            settings={arg: environ[var] for arg, var in spec.env.items() if environ.get(var)},
            timeout_s=config.timeout_s,
        )
    if not isinstance(built, ModelClient):
        raise TypeError(f"the client for {ref} does not implement the ModelClient port")
    return built


def build_embedding_client(config: GatewayConfig, ref: str, environ: Environ) -> EmbeddingClient:
    """The client for one embedding ``provider:model`` (S10)."""
    provider, model = split_ref(ref)
    spec = config.models[ref]
    if provider == "fake":
        built = registry.create(
            "embedding_client", "fake", model=model, dimensions=spec.dimensions or 1
        )
    else:
        provider_spec = config.providers[provider]
        key_env = provider_spec.key_env
        built = registry.create(
            "embedding_client",
            "langchain",
            provider=provider,
            model=model,
            api_key=environ.get(key_env) if key_env else None,
            key_env=key_env,
            params=spec.params,
            settings={
                arg: environ[var] for arg, var in provider_spec.env.items() if environ.get(var)
            },
            timeout_s=config.timeout_s,
        )
    if not isinstance(built, EmbeddingClient):
        raise TypeError(f"the client for {ref} does not implement the EmbeddingClient port")
    return built


def build_cache(choice: OptionChoice) -> ResponseCache:
    built = registry.create("model_cache", choice.option, **choice.params)
    if not isinstance(built, ResponseCache):
        raise TypeError(f"model_cache option {choice.option!r} does not implement ResponseCache")
    return built


def forget_offline_replies(choice: OptionChoice, document_key: str) -> None:
    """An offline worker may inherit a SQLite cache from an earlier model-enabled run."""
    if choice.option != "sqlite":
        return
    path = str(choice.params.get("path", ":memory:"))
    if not Path(path).is_file():
        return
    cache = SqliteCache(path)
    try:
        with cache.guard:
            cache.forget(document_key)
    finally:
        cache.close()


def build_cost_log(choice: OptionChoice) -> CostLog:
    built = registry.create("cost_log", choice.option, **choice.params)
    if not isinstance(built, CostLog):
        raise TypeError(f"cost_log option {choice.option!r} does not implement CostLog")
    return built


def max_spend(config: GatewayConfig, environ: Environ) -> float:
    value = environ.get(MAX_SPEND_ENV)
    if not value:
        return config.max_spend_usd
    try:
        cap = float(value)
    except ValueError:
        raise ValueError(f"{MAX_SPEND_ENV} must be a number of dollars, not {value!r}") from None
    if not math.isfinite(cap):
        raise ValueError(f"{MAX_SPEND_ENV} must be finite")
    if cap < 0:
        raise ValueError(f"{MAX_SPEND_ENV} must not be negative")
    return cap


def build_gateway(
    config: GatewayConfig,
    *,
    environ: Environ | None = None,
    clients: Mapping[str, ModelClient] | None = None,
    embedding_clients: Mapping[str, EmbeddingClient] | None = None,
    cache: ResponseCache | None = None,
    log: CostLog | None = None,
    clock: Callable[[], datetime] = utc_now,
    sleep: Callable[[float], None] | None = None,
) -> ModelGateway:
    """The gateway as configured. Tests and the demo replace any part."""
    env = os.environ if environ is None else environ
    used = config.used_models()
    chat = [ref for ref in used if config.models[ref].kind == "chat"]
    vectors = [ref for ref in used if config.models[ref].kind == "embedding"]
    built = (
        dict(clients)
        if clients is not None
        else {ref: build_client(config, ref, env) for ref in chat}
    )
    built_embedding = (
        dict(embedding_clients)
        if embedding_clients is not None
        else {ref: build_embedding_client(config, ref, env) for ref in vectors}
    )
    extra = {"sleep": sleep} if sleep is not None else {}
    return ModelGateway(
        config,
        built,
        cache if cache is not None else build_cache(config.cache),
        log if log is not None else build_cost_log(config.cost_log),
        embedding_clients=built_embedding,
        max_spend_usd=max_spend(config, env),
        clock=clock,
        **extra,
    )


class FreeRequests(BaseModel):
    """OpenRouter's own count of today's free-model requests (UTC day)."""

    model_config = ConfigDict(frozen=True)

    used: int
    limit: int
    remaining: int


def openrouter_free_requests(
    api_key: str, client: httpx.Client | None = None
) -> FreeRequests | None:
    """Ask OpenRouter how many free-model requests this account has left today.

    Our count comes from our own log; this is OpenRouter's, which also sees
    requests made outside the knowledge base. ``None`` if it cannot be read.
    """
    owned = client is None
    http = client or httpx.Client(timeout=10.0)
    try:
        response = http.get(OPENROUTER_KEY_URL, headers={"Authorization": f"Bearer {api_key}"})
        response.raise_for_status()
        daily = response.json().get("data", {}).get("free_model_daily_requests")
        return FreeRequests.model_validate(daily) if daily else None
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        if owned:
            http.close()
