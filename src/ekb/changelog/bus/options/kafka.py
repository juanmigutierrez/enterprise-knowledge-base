"""Kafka event bus: the in-memory bus's promises on a real, durable log (S17).

The local design's backbone (D4, D13): Apache Kafka in KRaft mode, one broker
in ``docker compose``. What the in-memory option keeps in Python lists, Kafka
keeps on disk, shared by every process: the connectors publish from
``ekb connect``, the worker reads from ``ekb worker``, the Knowledge API reads
the deletes from ``ekb api``, and a restarted process resumes where its group
committed.

**One topic, ``ekb.changes``**, created by the bus on first use (``compose.yaml``
turns automatic topic creation off), with a fixed number of partitions.

- **Order per document.** The bus picks the partition itself, with the same
  ``partition_for`` (a SHA-256 of ``document_key``) as the in-memory bus, so a
  document lands in the same partition in every process and in both options.
- **Kept forever.** ``cleanup.policy=delete`` with unlimited retention: a new
  group replays the whole history, as in memory. Compaction (keep only each
  document's latest event) is the option for scale: it bounds the log, at the
  price of replaying only the latest state.
- **Events as JSON**, keyed by ``document_key``, exactly as the contract
  serializes them; reading one back gives an equal ``ChangeEvent``.

**The port's three promises, on top of Kafka** (:mod:`ekb.changelog.bus.kafka`
does the Kafka calls):

1. *Order per document*: one partition per document, read in offset order;
   a poll merges the partitions by time without reordering any of them.
2. *At least once*: a poll reads from the group's committed offset and never
   moves it, so anything not committed is read again, by this process or by
   the next one.
3. *No skipping*: a commit must name the partition's next pending offset;
   anything later is refused before Kafka is asked.

The bus remembers each group's position and the events it handed out, so a
commit needs no read; the first poll of a group asks Kafka where it is.
"""

from __future__ import annotations

import heapq
import threading
from itertools import islice

from ekb.changelog.bus.kafka import ConfluentLog, KafkaLog, Record
from ekb.changelog.bus.options.in_memory import partition_for
from ekb.changelog.bus.port import (
    BusStats,
    Delivery,
    GroupPosition,
    OutOfOrderCommitError,
    UnknownDeliveryError,
)
from ekb.contracts import ChangeEvent
from ekb.platform.infra import endpoint, scoped
from ekb.platform.registry import register

TOPIC = "ekb.changes"
TOPIC_CONFIG = {"cleanup.policy": "delete", "retention.ms": "-1", "retention.bytes": "-1"}
"""Keep every event: replay is a new group reading from the start, as in memory."""


@register("bus", "kafka")
class KafkaEventBus:
    def __init__(
        self,
        partitions: int = 3,
        *,
        namespace: str | None = None,
        bootstrap: str | None = None,
        log: KafkaLog | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        if partitions <= 0:
            raise ValueError("partitions must be positive")
        self.topic = scoped(TOPIC, namespace)
        self._partitions = partitions
        self._log = log or ConfluentLog(endpoint("kafka", bootstrap), timeout_s)
        self._ready = False
        self._positions: dict[str, list[int]] = {}
        """Per group, per partition: the offset of its next pending event."""
        self._handed_out: dict[str, dict[tuple[int, int], ChangeEvent]] = {}
        """Per group, the events its polls returned and it has not committed yet."""
        self._lock = threading.RLock()

    @property
    def partitions(self) -> int:
        return self._partitions

    def _ensure_topic(self) -> None:
        if self._ready:
            return
        found = self._log.ensure_topic(self.topic, self._partitions, TOPIC_CONFIG)
        if found != self._partitions:
            raise ValueError(
                f"topic {self.topic} has {found} partitions, not {self._partitions}: "
                "a document's partition would change"
            )
        self._ready = True

    # ------------------------------------------------------------ publishing

    def publish(self, event: ChangeEvent) -> Delivery:
        self._ensure_topic()
        partition = partition_for(event.document_key, self._partitions)
        offset = self._log.produce(
            self.topic, partition, event.document_key.encode(), event.model_dump_json().encode()
        )
        return Delivery(partition, offset, event)

    # ------------------------------------------------------------ consuming

    def _position(self, group: str) -> list[int]:
        """The group's next offsets: remembered, or asked of Kafka the first time
        (a group that never committed starts at the earliest event still held)."""
        position = self._positions.get(group)
        if position is None:
            committed = self._log.committed(group, self.topic, self._partitions)
            marks = self._log.watermarks(self.topic, self._partitions)
            position = []
            for p in range(self._partitions):
                done = committed.get(p)
                position.append(done if done is not None else marks[p][0])
            self._positions[group] = position
        return position

    def poll(self, group: str, max_events: int = 100) -> list[Delivery]:
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        self._ensure_topic()
        with self._lock:
            position = self._position(group)
            marks = self._log.watermarks(self.topic, self._partitions)
            starts = {p: max(position[p], marks[p][0]) for p in range(self._partitions)}
            ends = {p: min(marks[p][1], starts[p] + max_events) for p in range(self._partitions)}
            records = self._log.read(group, self.topic, starts, ends)
            handed = self._handed_out.setdefault(group, {})
            per_partition: list[list[tuple[int, Delivery]]] = [[] for _ in range(self._partitions)]
            for record in records:
                delivery = _delivery(record)
                handed[(delivery.partition, delivery.offset)] = delivery.event
                per_partition[record.partition].append((record.timestamp_ms, delivery))
        # Each partition is in offset order; merging them by time keeps that order.
        merged = heapq.merge(*per_partition, key=lambda item: item[0])
        return [delivery for _, delivery in islice(merged, max_events)]

    def commit(self, group: str, delivery: Delivery) -> None:
        partition, offset = delivery.partition, delivery.offset
        if not 0 <= partition < self._partitions or offset < 0:
            raise UnknownDeliveryError(f"partition {partition} has no offset {offset}")
        with self._lock:
            event = self._handed_out.get(group, {}).get((partition, offset))
            if event is None:
                event = self._read_one(group, partition, offset)
            if event != delivery.event:
                raise UnknownDeliveryError(
                    f"event does not match partition {partition} offset {offset}"
                )
            position = self._position(group)
            next_offset = position[partition]
            if offset < next_offset:
                return  # already committed: a repeat after a redelivery is harmless
            if offset > next_offset:
                raise OutOfOrderCommitError(
                    f"group {group!r} cannot commit partition {partition} "
                    f"offset {offset}: offset {next_offset} is still pending, "
                    "and skipping it could lose a delete or a revocation"
                )
            self._log.commit(group, self.topic, partition, offset + 1)
            position[partition] = offset + 1
            self._handed_out.get(group, {}).pop((partition, offset), None)

    def _read_one(self, group: str, partition: int, offset: int) -> ChangeEvent:
        """The event at ``offset``, read from Kafka (a commit of an event this process
        did not poll, e.g. one ``publish`` returned)."""
        self._ensure_topic()
        low, high = self._log.watermarks(self.topic, self._partitions)[partition]
        if not low <= offset < high:
            raise UnknownDeliveryError(f"partition {partition} has no offset {offset}")
        (record,) = self._log.read(group, self.topic, {partition: offset}, {partition: offset + 1})
        return _delivery(record).event

    # ------------------------------------------------------------ looking

    def stats(self) -> BusStats:
        self._ensure_topic()
        marks = self._log.watermarks(self.topic, self._partitions)
        ends = tuple(marks[p][1] for p in range(self._partitions))
        positions = {
            group: [offsets.get(p, marks[p][0]) for p in range(self._partitions)]
            for group, offsets in self._log.groups(self.topic).items()
        }
        with self._lock:
            positions.update({group: list(done) for group, done in self._positions.items()})
        groups = tuple(
            GroupPosition(
                group=name,
                committed=tuple(done),
                lag=sum(end - d for end, d in zip(ends, done, strict=True)),
            )
            for name, done in sorted(positions.items())
        )
        return BusStats(end_offsets=ends, groups=groups)

    # ------------------------------------------------------------ lifecycle

    def close(self) -> None:
        """Close the consumers and flush the producer (the end of a process)."""
        self._log.close()

    def destroy(self) -> None:
        """Delete the topic (the end of a demo or test run), then close."""
        self._log.delete_topic(self.topic)
        self._ready = False
        self.close()


def _delivery(record: Record) -> Delivery:
    return Delivery(record.partition, record.offset, ChangeEvent.model_validate_json(record.value))
