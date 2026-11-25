"""Running T2: decide, act on the decision, then record it. The same for every option.

For each event, in the bus's order for its document::

    previous = registry.get(key)                    # what was last processed
    decision = differ.decide(previous, event)       # the option; pure
    purge the item's originals                      # deletes only
    downstream(result)                              # the next steps (the worker, S12)
    registry.put(record_after(previous, event))     # only now is it "processed"

**Why the registry is written last.** The bus delivers at least once, and T2
recognises a repeat by comparing with the registry. If the record were written
first and the worker crashed before the next steps had the decision, the
redelivered event would look like a repeat and be skipped: the change would be
lost. Written last, a crash means the event is decided again and handed on
again, which is harmless (every next step must accept repeats anyway).
A ``downstream`` that raises leaves the registry untouched, and ``drain`` does
not commit the event, so it is delivered again.

Deletes name the lifetime closed at publication. A retry removes only
originals from that lifetime, even when a later version is already queued.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ekb.changelog.rawstore.port import RawStore
from ekb.changelog.registry.port import DocumentRegistry, record_after
from ekb.contracts import ChangeEvent, DiffAction, DiffDecision
from ekb.process.t2_diff.port import Differ


@dataclass(frozen=True)
class DiffResult:
    decision: DiffDecision
    purged: int
    """Originals removed from the raw store (deletes only)."""


Downstream = Callable[[DiffResult], object]
"""Receives each result before it is recorded; raising means: not done, deliver again."""


def _nowhere(result: DiffResult) -> None:
    """No next step: T2 alone (S4's demo). The worker pipeline wires T3 … T8 (S12)."""


class DiffStep:
    """T2 as a bus consumer's handler: ``drain(bus, "t2_diff", lambda d: step.handle(d.event))``."""

    def __init__(
        self,
        differ: Differ,
        registry: DocumentRegistry,
        rawstore: RawStore,
        downstream: Downstream = _nowhere,
    ) -> None:
        self._differ = differ
        self._registry = registry
        self._rawstore = rawstore
        self._downstream = downstream

    def handle(self, event: ChangeEvent) -> DiffResult:
        previous = self._registry.get(event.document_key)
        decision = self._differ.decide(previous, event)
        purged = 0
        if decision.action is DiffAction.PURGE:
            purged = self._rawstore.purge(event.document_key, lifetime=event.original_lifetime)
        result = DiffResult(decision, purged)
        self._downstream(result)
        self._registry.put(record_after(previous, event))
        return result
