"""What goes into a model call, what comes out, and what the cost log keeps.

The cost log (:class:`CallRecord`) deliberately holds **no prompt and no reply**:
only the task, which model, the cache key (a hash), tokens, cost and outcome. It
can be shown in the inspector and kept for the budget without keeping any
document text, so a deleted document leaves nothing readable behind in it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Outcome = Literal["hit", "ok", "rate_limited", "unavailable", "invalid"]
"""``hit``: answered from the cache, no request made. ``ok``: the model answered.
``rate_limited``: the provider said "too many requests". ``unavailable``: the
provider failed or could not be reached; another model may work. ``invalid``:
the provider rejected the request itself; another model would too."""

FailureKind = Literal["rate_limited", "unavailable", "invalid"]


class ChatRequest(BaseModel):
    """One call, independent of the model that will answer it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt: str
    system: str = ""
    max_output_tokens: int = Field(ge=1)
    """Required (D16 #7): no call may produce an unbounded answer."""
    temperature: float = Field(default=0.0, ge=0)
    json_schema: dict[str, Any] | None = None
    """Ask for JSON matching this schema; the reply's ``text`` is that JSON."""


class ModelReply(BaseModel):
    """What a model client returns."""

    model_config = ConfigDict(frozen=True)

    text: str
    usage_reported: bool = True
    input_tokens: int = 0
    output_tokens: int = 0
    """As the provider bills them, thinking included for models that think."""
    served_model: str | None = None
    """The model the provider says answered, when it says (a router may pick one)."""


class CachedReply(BaseModel):
    """A reply kept under its cache key."""

    model_config = ConfigDict(frozen=True)

    candidate: str
    reply: ModelReply
    created_at: datetime


class CallRecord(BaseModel):
    """One line of the cost log: a cache hit or one attempt at one model."""

    model_config = ConfigDict(frozen=True)

    at: datetime
    task: str
    candidate: str
    prompt_version: str
    key: str
    """The cache key: a SHA-256 of the call, never the call itself."""
    outcome: Outcome
    quota: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = 0.0
    latency_ms: int = 0
    document_key: str | None = None
    error: str | None = None
    """For a failed attempt: a safe diagnostic, such as the SDK error type and status."""

    @property
    def requested(self) -> bool:
        """True when a request reached (or tried to reach) a provider."""
        return self.outcome != "hit"


class Step(BaseModel):
    """What the gateway did with one candidate during one call."""

    model_config = ConfigDict(frozen=True)

    candidate: str
    result: str
    """``hit``, ``ok``, ``skipped: <why>`` or ``<failure>: <detail>``."""


class GatewayReply(BaseModel):
    """What a caller gets back from :meth:`ModelGateway.complete`."""

    model_config = ConfigDict(frozen=True)

    text: str
    task: str
    candidate: str
    """The ``provider:model`` that wrote the reply (now, or when it was cached)."""
    served_model: str | None = None
    cached: bool
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    """What this call cost now: 0 for a cache hit."""
    latency_ms: int
    key: str
    steps: tuple[Step, ...]
    """Every candidate considered, in order, and what happened to it."""
