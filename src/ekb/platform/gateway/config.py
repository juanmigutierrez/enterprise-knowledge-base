"""The ``gateway:`` section of the settings: which models each task may use, and their limits.

Shape (``config/default.yaml``)::

    gateway:
      cache:    { option: sqlite, params: { path: .cache/ekb/model-cache.sqlite } }
      cost_log: { option: jsonl,  params: { path: .cache/ekb/model-calls.jsonl } }
      max_spend_usd: 25
      providers:                      # how to reach each provider; keys from the environment
        groq: { key_env: GROQ_API_KEY }
      quotas:                         # free-tier limits; several models may share one
        groq_gpt_oss_20b: { per_minute: 30, per_day: 1000, resets: UTC }
      models:                         # every model a task may use: price, quota, settings
        "groq:openai/gpt-oss-20b": { quota: groq_gpt_oss_20b, params: { reasoning_effort: low } }
      tasks:                          # task -> candidates, tried in order
        context_header: ["google_genai:gemini-3.5-flash-lite", "groq:openai/gpt-oss-20b"]

A model is named ``provider:model`` (``openrouter:openrouter/free``); only the
first ``:`` separates them, because some model IDs contain one
(``ollama:gemma4:e4b``). Everything a task names must be declared, so a typo
fails when the settings load, not on the first call.
"""

from __future__ import annotations

from typing import Any, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PER_MILLION = 1_000_000


def split_ref(ref: str) -> tuple[str, str]:
    """``"ollama:gemma4:e4b"`` -> ``("ollama", "gemma4:e4b")``."""
    provider, sep, model = ref.partition(":")
    if not sep or not provider or not model:
        raise ValueError(f"model {ref!r} must be written provider:model")
    return provider, model


class OptionChoice(BaseModel):
    """Which option of a gateway port to build, like a step's ``option`` and ``params``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    option: str
    params: dict[str, Any] = Field(default_factory=dict)


class Price(BaseModel):
    """USD per million tokens. Free tiers are 0; a paid model must say what it costs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    output: float = Field(default=0.0, ge=0, allow_inf_nan=False)

    @property
    def free(self) -> bool:
        return self.input == 0 and self.output == 0

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input + output_tokens * self.output) / PER_MILLION


class Quota(BaseModel):
    """A free tier's request limits. ``None`` means no limit of that kind.

    ``resets`` is the time zone whose midnight starts a new day for ``per_day``:
    Google's free tier resets at midnight Pacific time, OpenRouter's at midnight UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    per_minute: int | None = Field(default=None, ge=0)
    per_day: int | None = Field(default=None, ge=0)
    resets: str = "UTC"

    @field_validator("resets")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {value!r} (use an IANA name)") from exc
        return value


class ModelSpec(BaseModel):
    """One model a task may use."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    price: Price = Price()
    quota: str | None = None
    """The quota it counts against; models that share a free tier share a quota."""
    params: dict[str, Any] = Field(default_factory=dict)
    """Extra settings for the model (``reasoning_effort: low``). They change the
    reply, so they are part of the cache key."""


class ProviderSpec(BaseModel):
    """How to reach a provider. Secrets are never here, only the names of the
    environment variables that hold them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key_env: str | None = None
    """The variable holding the API key; ``None`` when the provider needs none."""
    env: dict[str, str] = Field(default_factory=dict)
    """Other settings from the environment: chat-model argument -> variable,
    e.g. ``{base_url: OLLAMA_URL}``. Passed only when the variable is set."""


class GatewayConfig(BaseModel):
    """Everything the gateway needs to know, checked as one whole."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cache: OptionChoice = OptionChoice(option="in_memory")
    cost_log: OptionChoice = OptionChoice(option="in_memory")
    max_spend_usd: float = Field(default=25.0, ge=0, allow_inf_nan=False)
    """Paid calls stop once the cost log adds up to this (``EKB_MAX_SPEND_USD``
    overrides it). Free models and cached replies are never blocked."""
    timeout_s: float = Field(default=60.0, gt=0)
    max_wait_s: float = Field(default=20.0, ge=0)
    """How long a call may wait for a per-minute slot before trying the next model."""
    cooldown_s: float = Field(default=600.0, ge=0)
    """After a provider says "rate limited", how long to leave that quota alone."""
    providers: dict[str, ProviderSpec] = Field(default_factory=dict)
    quotas: dict[str, Quota] = Field(default_factory=dict)
    models: dict[str, ModelSpec] = Field(default_factory=dict)
    tasks: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _references_exist(self) -> Self:
        for ref, spec in self.models.items():
            provider, _ = split_ref(ref)
            if provider not in self.providers:
                raise ValueError(f"model {ref!r}: provider {provider!r} is not under providers")
            if (
                provider not in {"fake", "ollama"}
                and spec.quota is None
                and "price" not in spec.model_fields_set
            ):
                raise ValueError(f"model {ref!r} needs a free-tier quota or an explicit price")
            if spec.quota is not None and spec.quota not in self.quotas:
                raise ValueError(f"model {ref!r}: quota {spec.quota!r} is not under quotas")
        for task, candidates in self.tasks.items():
            if not candidates:
                raise ValueError(f"task {task!r} has no candidates")
            if len(set(candidates)) != len(candidates):
                raise ValueError(f"task {task!r} lists a candidate twice")
            for ref in candidates:
                if ref not in self.models:
                    raise ValueError(f"task {task!r}: model {ref!r} is not under models")
        return self

    def used_models(self) -> list[str]:
        """Every model some task may call, in first-seen order."""
        seen: dict[str, None] = {}
        for candidates in self.tasks.values():
            seen.update(dict.fromkeys(candidates))
        return list(seen)
