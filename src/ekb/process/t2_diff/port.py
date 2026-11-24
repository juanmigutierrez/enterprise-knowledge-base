"""Port for T2 · Diff. Every diff option implements :class:`Differ`.

T2 is the first worker step. For each change event it asks one question: given
what the knowledge base last processed for this item (its registry record),
what is left to do? The answer is a :class:`~ekb.contracts.DiffDecision`:
process, ACL fast lane, skip or purge.

Why it matters:

- **No re-processing.** The bus delivers at least once, the webhook and the
  full listing can report the same change, and a source can save identical
  text as a new version. Parsing, enriching and embedding again would cost
  time and money (D16) for the same result.
- **Permissions move fast.** A permissions-only change needs no parsing or
  embedding, only T8; the fast lane gets it there without waiting behind the
  content work.
- **Deletes are never skipped.** A delete goes to every store, whatever the
  registry says.

``decide`` is pure: no I/O and no side effects, so options can be compared on
the same inputs and tested exhaustively. Acting on the decision (purging
originals, recording the registry) is :class:`~ekb.process.t2_diff.step.DiffStep`'s
job and is the same for every option.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ekb.changelog.registry.port import DocumentRecord
from ekb.contracts import ChangeEvent, DiffDecision


@runtime_checkable
class Differ(Protocol):
    """Decides what an event needs, given the item's last processed record."""

    def decide(self, previous: DocumentRecord | None, event: ChangeEvent) -> DiffDecision:
        """``previous`` is ``None`` if the item was never processed; it may be a tombstone."""
        ...
