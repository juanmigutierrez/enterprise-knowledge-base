"""Port for 3 · Change log: the event bus. Every bus option implements :class:`EventBus`.

Connectors publish every ``ChangeEvent`` here; workers, the registry recorder
and the inspector consume them. Whatever the technology (Kafka locally, SQS
FIFO on AWS, D4), every option keeps three promises:

1. **Order per document.** All events of one ``document_key`` go to the same
   partition, in the order they were published, and are delivered in that
   order. Events of *different* documents may interleave in any order; nothing
   downstream may rely on that.
2. **At least once.** An event stays pending for a consumer group until the
   group commits it. Anything not committed is delivered again, so a worker
   that crashes mid-batch loses nothing, and must accept repeats (T2 skips
   them by content hash, S4).
3. **No skipping.** A group commits each partition's events strictly in order.
   Committing a later event while an earlier one is still pending is refused:
   the earlier one could be a delete or a revocation.

**Consumer groups** are independent readers of the same stream: each keeps its
own position, so the registry recorder, the inspector and a re-indexing job do
not get in each other's way.

**Replay is a new group.** A group that has never committed starts from the
earliest event the bus still holds (Kafka's ``auto.offset.reset=earliest``).
The in-memory option holds everything; Kafka holds events until retention or
compaction removes them. SQS forgets a message once it is consumed, which is
why production rebuilds from the raw store and the registry instead (D4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ekb.contracts import ChangeEvent


@dataclass(frozen=True)
class Delivery:
    """One event at its place in the bus: ``offset`` counts from 0 within ``partition``."""

    partition: int
    offset: int
    event: ChangeEvent


@dataclass(frozen=True)
class GroupPosition:
    """Where a consumer group is: per partition, the offset of its next pending event."""

    group: str
    committed: tuple[int, ...]
    lag: int
    """Events published but not yet committed by this group, over all partitions."""


@dataclass(frozen=True)
class BusStats:
    """What the bus holds and how far each group has read (for the inspector and demos)."""

    end_offsets: tuple[int, ...]
    """Per partition, how many events it holds (the offset the next event will get)."""
    groups: tuple[GroupPosition, ...]

    @property
    def events(self) -> int:
        return sum(self.end_offsets)


class OutOfOrderCommitError(ValueError):
    """A group tried to commit an event while an earlier one in its partition was pending."""


class UnknownDeliveryError(LookupError):
    """A commit named a partition or offset this bus never handed out."""


@runtime_checkable
class EventBus(Protocol):
    """Publishes change events and delivers them to consumer groups, in order per document."""

    @property
    def partitions(self) -> int:
        """How many partitions events are spread over (by ``document_key``)."""
        ...

    def publish(self, event: ChangeEvent) -> Delivery:
        """Append ``event`` to its document's partition and say where it landed."""
        ...

    def poll(self, group: str, max_events: int = 100) -> list[Delivery]:
        """The group's pending events, oldest first: everything after its last commit.

        Polling does not move the group forward, only :meth:`commit` does, so
        polling again before committing returns the same events.
        """
        ...

    def commit(self, group: str, delivery: Delivery) -> None:
        """Mark ``delivery`` done for ``group``. Must be the partition's next pending event.

        Committing an event that is already committed does nothing (a repeat is
        harmless); committing past a pending one raises
        :class:`OutOfOrderCommitError`.
        """
        ...

    def stats(self) -> BusStats:
        """Partition sizes and every group's position."""
        ...
