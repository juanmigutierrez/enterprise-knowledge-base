"""The worker's dead letters (S12): events set aside after failing too many times.

Without them, one event that always fails (an original no parser can read, a
bug in an option) would block every event behind it forever: the bus never
skips an event, because the next one could be a delete or a revocation (S3).
So after ``max_attempts`` failures the worker **parks** the event here, commits
it on the bus, and moves on. Four rules keep that safe:

1. **Parking fails closed.** The item is withdrawn first: its chunks leave the
   search store, its readers record and (for an identity user) its membership
   go. Until it is fixed, no one reads a version that may be out of date.
2. **Order per item is kept.** Later events of a parked item are parked behind
   it, in bus order, never applied around it (an older change replayed after a
   newer one would undo it).
3. **A delete is never parked.** It purges the item as usual and discards what
   was parked for it: a delete supersedes every change before it. If the delete
   itself keeps failing, it stays pending and blocks, as before: a delete is
   never set aside.
4. **A retry replays in order** (:meth:`ekb.app.pipeline.Pipeline.retry_parked`);
   if nothing in the replay republished the item, it is rebuilt from its last
   stored original (the change log is the replay source, D4).

In production this is SQS's redrive policy (a dead-letter queue) or a Kafka
dead-letter topic; neither keeps order per item by itself, which is why rule 2
lives in the worker. This one is in memory: it is lost when the process stops,
and the events with it, so the next full listing is what repairs the item then.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from ekb.changelog.bus.port import Delivery


@dataclass(frozen=True)
class Parked:
    """One event set aside, and why."""

    delivery: Delivery
    error: str
    """The last failure, in one line (empty for an event parked behind another)."""
    parked_at: datetime


class DeadLetters:
    """Parked events per item, oldest first, and the items withdrawn by parking."""

    def __init__(self) -> None:
        self._parked: dict[str, list[Parked]] = {}
        self._withdrawn: set[str] = set()
        self._lock = threading.Lock()

    def park(self, delivery: Delivery, error: str = "") -> int:
        """Set ``delivery`` aside behind its item's other parked events; return how many
        events the item now has parked."""
        key = delivery.event.document_key
        with self._lock:
            queue = self._parked.setdefault(key, [])
            queue.append(Parked(delivery, error, datetime.now(UTC)))
            return len(queue)

    def withdraw(self, document_key: str) -> None:
        """Mark the item as withdrawn by parking: it must be republished on retry."""
        with self._lock:
            self._withdrawn.add(document_key)

    def restored(self, document_key: str) -> None:
        """The item was processed or purged since: nothing to rebuild for it."""
        with self._lock:
            self._withdrawn.discard(document_key)

    def withdrawn(self, document_key: str) -> bool:
        with self._lock:
            return document_key in self._withdrawn

    def holds(self, document_key: str) -> bool:
        """Whether the item has parked events (so its next event must wait behind them)."""
        with self._lock:
            return bool(self._parked.get(document_key))

    def first(self, document_key: str) -> Parked | None:
        """The event the item waits behind, if any."""
        with self._lock:
            queue = self._parked.get(document_key)
            return queue[0] if queue else None

    def take(self, document_key: str) -> list[Parked]:
        """Remove and return the item's parked events, oldest first (a retry or a delete)."""
        with self._lock:
            return self._parked.pop(document_key, [])

    def put_back(self, document_key: str, parked: list[Parked]) -> None:
        """Return events a retry could not finish, ahead of anything parked since."""
        with self._lock:
            self._parked[document_key] = parked + self._parked.get(document_key, [])

    def items(self) -> dict[str, tuple[Parked, ...]]:
        """Every item with parked events, by key."""
        with self._lock:
            return {key: tuple(queue) for key, queue in sorted(self._parked.items()) if queue}

    def __len__(self) -> int:
        with self._lock:
            return sum(len(queue) for queue in self._parked.values())
