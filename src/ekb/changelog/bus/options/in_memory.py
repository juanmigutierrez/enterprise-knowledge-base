"""In-memory event bus: Kafka's model in about a hundred lines, no broker needed.

- A **partition** is a Python list; an event's offset is its index in it.
- An event goes to partition ``sha256(document_key) mod N``. A cryptographic
  hash, not Python's ``hash()``, because Python salts string hashes per
  process: the same document must land in the same partition every time,
  in every process (Kafka uses murmur2 for the same reason).
- A **consumer group** is just a list of committed offsets, one per partition.

It keeps every event forever (no retention, no compaction), so a new group
always replays the full history. It lives in one process and is lost when the
process stops; Kafka (S17) is the durable option behind the same port.
It is thread-safe: in ``ekb inspector`` connectors publish from one thread
while the API reads from others.
"""

from __future__ import annotations

import hashlib
import heapq
import threading
from dataclasses import dataclass
from itertools import islice

from ekb.changelog.bus.port import (
    BusStats,
    Delivery,
    GroupPosition,
    OutOfOrderCommitError,
    UnknownDeliveryError,
)
from ekb.contracts import ChangeEvent
from ekb.platform.registry import register


def partition_for(document_key: str, partitions: int) -> int:
    """Stable partition of a document: the same key, the same partition, in any process."""
    digest = hashlib.sha256(document_key.encode()).digest()
    return int.from_bytes(digest[:8], "big") % partitions


@dataclass(frozen=True)
class _Entry:
    seq: int  # publish order across all partitions, only to return polls oldest first
    event: ChangeEvent


@register("bus", "in_memory")
class InMemoryEventBus:
    def __init__(self, partitions: int = 3) -> None:
        if partitions <= 0:
            raise ValueError("partitions must be positive")
        self._partitions: list[list[_Entry]] = [[] for _ in range(partitions)]
        self._committed: dict[str, list[int]] = {}
        self._published = 0
        self._lock = threading.Lock()

    @property
    def partitions(self) -> int:
        return len(self._partitions)

    def publish(self, event: ChangeEvent) -> Delivery:
        partition = partition_for(event.document_key, self.partitions)
        with self._lock:
            log = self._partitions[partition]
            log.append(_Entry(self._published, event))
            self._published += 1
            return Delivery(partition, len(log) - 1, event)

    def poll(self, group: str, max_events: int = 100) -> list[Delivery]:
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        with self._lock:
            committed = self._position(group)
            pending = [
                [
                    (entry.seq, Delivery(partition, offset, entry.event))
                    for offset, entry in enumerate(log[start : start + max_events], start=start)
                ]
                for partition, (log, start) in enumerate(
                    zip(self._partitions, committed, strict=True)
                )
            ]
        # Each partition is already in order. Merging them by publish order keeps
        # that, and reads naturally: what happened, in the order it happened.
        merged = heapq.merge(*pending, key=lambda item: item[0])
        return [delivery for _, delivery in islice(merged, max_events)]

    def commit(self, group: str, delivery: Delivery) -> None:
        with self._lock:
            if not 0 <= delivery.partition < self.partitions or not (
                0 <= delivery.offset < len(self._partitions[delivery.partition])
            ):
                raise UnknownDeliveryError(
                    f"partition {delivery.partition} has no offset {delivery.offset}"
                )
            if self._partitions[delivery.partition][delivery.offset].event != delivery.event:
                raise UnknownDeliveryError(
                    f"event does not match partition {delivery.partition} offset {delivery.offset}"
                )
            committed = self._position(group)
            next_offset = committed[delivery.partition]
            if delivery.offset < next_offset:
                return  # already committed: a repeat after a redelivery is harmless
            if delivery.offset > next_offset:
                raise OutOfOrderCommitError(
                    f"group {group!r} cannot commit partition {delivery.partition} "
                    f"offset {delivery.offset}: offset {next_offset} is still pending, "
                    "and skipping it could lose a delete or a revocation"
                )
            committed[delivery.partition] = next_offset + 1

    def stats(self) -> BusStats:
        with self._lock:
            ends = tuple(len(log) for log in self._partitions)
            groups = tuple(
                GroupPosition(
                    group=name,
                    committed=tuple(offsets),
                    lag=sum(end - done for end, done in zip(ends, offsets, strict=True)),
                )
                for name, offsets in sorted(self._committed.items())
            )
        return BusStats(end_offsets=ends, groups=groups)

    def _position(self, group: str) -> list[int]:
        """The group's committed offsets; a new group starts at 0, the earliest event."""
        return self._committed.setdefault(group, [0] * self.partitions)
