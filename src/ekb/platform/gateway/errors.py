"""What can go wrong in a model call, in the gateway's own words.

Clients turn each library's errors into :class:`ModelCallError` with one of three
kinds, so the gateway can decide without knowing any provider:

- ``rate_limited``: "too many requests". Try the next candidate, and leave this
  quota alone for a while (``cooldown_s``).
- ``unavailable``: the provider failed, timed out, refused the key or no longer
  has the model. Try the next candidate.
- ``invalid``: the provider rejected the request itself (HTTP 400). Another model
  would reject it too, so the gateway stops and raises
  :class:`InvalidModelRequestError`: it is a bug to fix, not an outage to route around.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from ekb.platform.gateway.models import FailureKind, Step


class GatewayError(RuntimeError):
    """Base class: every error the gateway raises to a caller."""


class UnknownTaskError(GatewayError, LookupError):
    """The caller asked for a task the configuration does not route."""


class ModelCallError(GatewayError):
    """Raised by a client: the call failed, sorted into one of three kinds."""

    def __init__(
        self,
        kind: FailureKind,
        detail: str,
        *,
        received_reply: bool = False,
        usage: tuple[int, int] | None = None,
    ) -> None:
        self.received_reply = received_reply
        self.usage = usage
        super().__init__(f"{kind}: {detail}")
        self.kind: FailureKind = kind
        self.detail = detail


class InvalidModelRequestError(GatewayError):
    """A provider rejected the request itself; trying another model would not help."""

    def __init__(self, task: str, candidate: str, detail: str) -> None:
        super().__init__(f"{task}: {candidate} rejected the request ({detail})")
        self.task = task
        self.candidate = candidate


class NoModelAvailableError(GatewayError):
    """Every candidate for the task was skipped or failed."""

    def __init__(self, task: str, steps: Sequence[Step], next_reset: datetime | None) -> None:
        lines = "; ".join(f"{s.candidate}: {s.result}" for s in steps)
        when = (
            f" The first daily quota resets at {next_reset:%Y-%m-%d %H:%M} UTC."
            if next_reset
            else ""
        )
        super().__init__(f"no model available for {task!r}: {lines}.{when}")
        self.task = task
        self.steps = tuple(steps)
        self.next_reset = next_reset
