"""The inspector's **Lifecycle** screen (S16): apply one scripted change, see its effect.

The story is :mod:`ekb.app.lifecycle`'s eight changes (add, edit, revoke on the
page, revoke by group, a hire in two steps, a leaver, a delete). For each one
the screen shows the same questions three times:

- **before**: as answered just before the change;
- **right after**: as answered once the source changed but *before the worker
  ran*. The index is stale here, so this column is where a revocation or a
  delete must already hold: Q4 asks the source now;
- **now**: as answered at this moment, after the worker caught up.

and what the worker did in between (its traces: no content, S12).

**The one write route.** D19 makes the inspector read-only, and it still is
for the knowledge base: nothing here writes to a store. The screen's buttons
change a **mock source**, as its owner would through the source's own API (the
same call ``ekb demo lifecycle`` makes), and only the scripted changes, in their
order; the connectors and the worker do the rest. Applying a change holds the
sync loop for the moment it takes (:attr:`LifecyclePanel.paused`), so "right
after" is truly before the worker ran.

It does not wire anything itself (``ekb.operate`` cannot import ``ekb.app``):
the actions, how to apply them, the query path and the pause come in from
``ekb.app.inspector``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ekb.contracts import AnswerResult, Chunk
from ekb.operate.inspector.search import Ask
from ekb.operate.inspector.trace import LoggedTrace, TraceLog


@dataclass(frozen=True)
class ActionInfo:
    """One scripted change, as the screen describes it (``ekb.app.lifecycle.Action``)."""

    id: str
    title: str
    actor: str
    source: str
    change: str
    caught_by: str
    listing: bool
    expect: str
    after: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProbeInfo:
    """A question asked before and after each change, and the page it depends on."""

    user: str
    question: str
    page: str


class LifecycleRefusedError(ValueError):
    """The action cannot run now: already applied, or out of the story's order."""


class LifecycleSourceError(RuntimeError):
    """The mock source refused or did not answer: nothing was recorded."""


@dataclass(frozen=True)
class Probed:
    """One probe's answer at one moment."""

    probe: ProbeInfo
    result: AnswerResult


@dataclass(frozen=True)
class Applied:
    """One change applied from the screen."""

    number: int
    action: ActionInfo
    applied_at: datetime
    traces_from: int
    """The trace log's cursor just before the change: its traces start here."""
    before: tuple[Probed, ...]
    right_after: tuple[Probed, ...]


Apply = Callable[[str], None]
"""``apply(action_id)``: change the source; raises the two errors above."""
Pause = Callable[[], AbstractContextManager[object]]
"""Holds the sync loop while it is open."""


@dataclass
class LifecyclePanel:
    actions: Sequence[ActionInfo]
    probes: Sequence[ProbeInfo]
    apply: Apply
    ask: Ask
    paused: Pause
    traces: TraceLog
    linked: Callable[[str], Sequence[Chunk]]
    """A page's published chunks (the search store), for the links it carries now."""
    watched: str = ""
    """The page whose links the screen shows (the story's mentoring page)."""
    wired: Callable[[], bool] = lambda: True
    """Whether the sources are running yet (the inspector starts them after its API)."""
    _applied: list[Applied] = field(default_factory=list, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def action(self, action_id: str) -> ActionInfo | None:
        return next((a for a in self.actions if a.id == action_id), None)

    def applied(self) -> list[Applied]:
        with self._lock:
            return list(self._applied)

    def applied_ids(self) -> list[str]:
        return [a.action.id for a in self.applied()]

    def available(self, action: ActionInfo) -> str:
        """Why ``action`` can't run now, or ``""`` if it can."""
        done = self.applied_ids()
        if action.id in done:
            return "already applied"
        missing = [a for a in action.after if a not in done]
        if missing:
            return f"needs {', '.join(missing)} first"
        return ""

    def probe(self) -> tuple[Probed, ...]:
        return tuple(Probed(p, self.ask(p.user, p.question)) for p in self.probes)

    def run(self, action_id: str) -> Applied:
        """Apply one change, between a "before" and a "right after" round of questions,
        with the sync loop held so the worker cannot run in between."""
        action = self.action(action_id)
        if action is None:
            raise LookupError(action_id)
        with self.paused():
            why = self.available(action)
            if why:
                raise LifecycleRefusedError(f"{action_id}: {why}")
            before = self.probe()
            cursor = self.traces.cursor
            at = datetime.now(UTC)
            self.apply(action_id)
            try:
                right_after = self.probe()
            except Exception:
                # The source already committed. A later poll can observe "now", but
                # cannot reconstruct this moment once the worker resumes.
                logging.getLogger(__name__).exception(
                    "Post-change observation failed for %s", action_id
                )
                right_after = ()
            with self._lock:
                step = Applied(len(self._applied) + 1, action, at, cursor, before, right_after)
                self._applied.append(step)
            return step

    def traces_of(self, step: Applied) -> list[LoggedTrace]:
        """What the worker did after this change and before the next one."""
        steps = self.applied()
        later = next((s for s in steps if s.number == step.number + 1), None)
        entries = self.traces.entries(since=step.traces_from)
        if later is None:
            return entries
        return [e for e in entries if e.seq <= later.traces_from]

    def links(self) -> list[str]:
        if not self.watched:
            return []
        return sorted({e for chunk in self.linked(self.watched) for e in chunk.entity_ids})
