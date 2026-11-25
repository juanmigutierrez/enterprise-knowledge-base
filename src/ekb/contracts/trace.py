"""Pipeline trace contract: what the worker did with one change event, step by step.

The worker pipeline (S12, ``ekb.app.pipeline``) writes one :class:`Trace` per
attempt at an event: T2's decision, then every step that ran after it (T3 … T8,
the fast lane, the entity or permission store, the purge), each with what it
did in plain words and how long it took. ``ekb demo ingest`` prints them and the
inspector's **Pipeline** tab shows them, so a page can be followed from its
source to the search store.

It lives here because its readers sit in layers that may not import the
pipeline: the inspector (``ekb.operate``) shows traces the composition root
(``ekb.app``) writes.

**A trace holds no content.** Keys, versions, decisions, counts, option and
entity IDs, readers and errors: never a passage, a title or a name. So a
deleted page's traces can stay, as the history of what happened to it, without
breaking the rule that deleted content is never returned.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from ekb.contracts.diff import DiffAction
from ekb.contracts.events import ChangeOp

STEP_ORDER: tuple[str, ...] = (
    "rebuild",
    "T2 diff",
    "T8 readers first",
    "T3 parse",
    "T4 chunk",
    "T5 classify",
    "T6 entities",
    "T7 embed",
    "T8 publish",
    "T8 fast lane",
    "entity store",
    "permission store",
    "no store yet",
    "purge search",
    "purge entity",
    "purge model cache",
    "finish",
    "dead letters",
)
"""Every step name the worker writes, in the order data meets them (for totals and the UI)."""


def step_rank(step: str) -> int:
    """Where ``step`` sorts in :data:`STEP_ORDER`; an unknown name sorts last."""
    return STEP_ORDER.index(step) if step in STEP_ORDER else len(STEP_ORDER)


class StepOutcome(StrEnum):
    """How one step ended."""

    DONE = "done"
    FAILED = "failed"
    """It raised: nothing after it ran, nothing new was kept, the event comes back."""


class StepTrace(BaseModel):
    """One step of one attempt: ``T3 parse``, ``T8 publish``, ``purge`` …"""

    model_config = ConfigDict(frozen=True)

    step: str
    outcome: StepOutcome
    detail: str
    """What the step did, or why it failed, in plain words (no content)."""
    ms: float
    """How long it took, in milliseconds."""


class TraceStatus(StrEnum):
    """Where one attempt left the event's item."""

    PUBLISHED = "published"
    """A new version of a document is in the search store, with its readers."""
    READERS = "readers"
    """Only the readers changed (T2's fast lane); nothing was parsed or embedded."""
    RECORD = "record"
    """A structured record: it updated the entity or permission store, or nothing yet."""
    SKIPPED = "skipped"
    """Nothing new: a repeat, or a new version with the same text and readers."""
    PURGED = "purged"
    """Deleted in the source: removed from every store."""
    FAILED = "failed"
    """A step raised. Nothing was recorded, so the event is delivered again."""
    PARKED = "parked"
    """Set aside in the dead letters: after too many failed attempts, or behind one."""
    REBUILT = "rebuilt"
    """Processed again from its stored original, after its dead letters were retried."""


class Trace(BaseModel):
    """One attempt at one event, from T2's decision to the last store it touched."""

    model_config = ConfigDict(frozen=True)

    document_key: str
    source: str
    op: ChangeOp
    version: str
    partition: int | None
    offset: int | None
    """Where the event sits in the bus: with ``partition``, it names one delivery.
    ``None`` for a rebuild from the stored original, which no event delivered."""
    attempt: int
    """1 for the first try; a failed event is delivered again with the next number."""
    action: DiffAction | None
    """T2's decision; ``None`` when T2 did not decide (it failed, or the event was parked)."""
    status: TraceStatus
    steps: tuple[StepTrace, ...]
    error: str = ""
    """Why the attempt failed, in one line; empty when it didn't."""
    started_at: datetime
    ms: float
    """The whole attempt, in milliseconds."""

    @property
    def failed_step(self) -> str | None:
        """The step that raised, if one did."""
        return next((s.step for s in self.steps if s.outcome is StepOutcome.FAILED), None)
