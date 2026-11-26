"""Kafka, reduced to the few log operations the bus needs (S17).

:class:`~ekb.changelog.bus.options.kafka.KafkaEventBus` keeps the port's
promises (order per document, at least once, no skipping) in plain Python on
top of :class:`KafkaLog`: append a record to a partition, read a range of
offsets, read and write a consumer group's committed offsets, and look at the
partitions' first and next offsets. :class:`ConfluentLog` does those with
confluent-kafka (librdkafka), and is the only module that imports it (an
import-linter contract). Tests swap in a fake log, so the bus's own logic is
tested without a broker; the integration tests run the same promises against
the real Kafka in ``docker compose``.

Why so little of Kafka's consumer API is used:

- **No subscriptions, no rebalancing.** A consumer is *assigned* every
  partition of the topic and moved to the offsets the bus asks for on every
  read. Kafka still stores the group's committed offsets (``group.id``), which
  is all a consumer group means in the port: a named position.
- **Nothing is committed automatically.** ``enable.auto.commit`` is off; the
  bus commits one event at a time, in order, after the handler returned.
- **Synchronous publishing.** ``produce`` waits for the broker's
  acknowledgement (``acks=all``, idempotent producer), because the change log
  needs the event's offset before it records the event as accepted.

Every failure to reach the broker becomes
:class:`~ekb.platform.infra.InfrastructureError`, which the sync loops retry.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from ekb.platform.infra import InfrastructureError

if TYPE_CHECKING:
    from confluent_kafka import Consumer, Producer
    from confluent_kafka.admin import AdminClient

WATERMARK_GROUP = "ekb-watermarks"
"""The group the adapter uses only to look at partitions; it never commits."""


@dataclass(frozen=True)
class Record:
    """One record of a partition: where it is, when it was written, and its bytes."""

    partition: int
    offset: int
    timestamp_ms: int
    value: bytes


class KafkaLog(Protocol):
    """The log operations the bus is built on."""

    def ensure_topic(self, topic: str, partitions: int, config: Mapping[str, str]) -> int:
        """Create the topic if it is missing; return how many partitions it has."""
        ...

    def produce(self, topic: str, partition: int, key: bytes, value: bytes) -> int:
        """Append one record to ``partition`` and wait for the broker; return its offset."""
        ...

    def read(
        self, group: str, topic: str, starts: Mapping[int, int], ends: Mapping[int, int]
    ) -> list[Record]:
        """Every record from ``starts[p]`` (included) to ``ends[p]`` (excluded), by
        partition then offset. ``group`` names the consumer that reads."""
        ...

    def watermarks(self, topic: str, partitions: int) -> dict[int, tuple[int, int]]:
        """Per partition: the first offset still held, and the offset the next record gets."""
        ...

    def committed(self, group: str, topic: str, partitions: int) -> dict[int, int | None]:
        """Per partition, the group's next offset to read, or ``None`` if it never committed."""
        ...

    def commit(self, group: str, topic: str, partition: int, next_offset: int) -> None:
        """Store ``next_offset`` as the group's position in ``partition``."""
        ...

    def groups(self, topic: str) -> dict[str, dict[int, int]]:
        """Every consumer group with a committed offset in ``topic``: partition -> offset."""
        ...

    def delete_topic(self, topic: str) -> None: ...

    def close(self) -> None: ...


@contextmanager
def _reaching(what: str) -> Iterator[None]:
    """Kafka's own exceptions, and an admin request that never answered (its future's
    ``TimeoutError``), -> :class:`InfrastructureError`, with what was being done."""
    from confluent_kafka import KafkaException

    try:
        yield
    except (KafkaException, BufferError) as exc:
        raise InfrastructureError(f"kafka: {what}: {exc}") from exc
    except TimeoutError as exc:
        raise InfrastructureError(f"kafka: {what}: no answer in time") from exc


@contextmanager
def _timing_out(what: str) -> Iterator[None]:
    """An admin future that never answered -> :class:`InfrastructureError` (its own
    ``TimeoutError`` has no message, and is an ``OSError`` callers would misread)."""
    try:
        yield
    except TimeoutError as exc:
        raise InfrastructureError(f"kafka: {what}: no answer in time") from exc


class ConfluentLog:
    """:class:`KafkaLog` with confluent-kafka. Building it opens no connection: the
    producer, the consumers (one per group) and the admin client are made on first use."""

    def __init__(self, bootstrap: str, timeout_s: float = 10.0) -> None:
        self.bootstrap = bootstrap
        self.timeout_s = timeout_s
        self._producer: Producer | None = None
        self._admin: AdminClient | None = None
        self._consumers: dict[str, Consumer] = {}

    # ------------------------------------------------------------ clients

    def _common(self) -> dict[str, Any]:
        ms = int(self.timeout_s * 1000)
        # librdkafka logs every failed connection attempt on its own; the bus reports
        # the failure once, as an InfrastructureError, so only critical logs are kept.
        return {"bootstrap.servers": self.bootstrap, "socket.timeout.ms": ms, "log_level": 2}

    def _get_producer(self) -> Producer:
        if self._producer is None:
            from confluent_kafka import Producer

            self._producer = Producer(
                {
                    **self._common(),
                    "enable.idempotence": True,
                    "acks": "all",
                    "linger.ms": 0,
                    "message.timeout.ms": int(self.timeout_s * 1000),
                }
            )
        return self._producer

    def _get_admin(self) -> AdminClient:
        if self._admin is None:
            from confluent_kafka.admin import AdminClient

            self._admin = AdminClient(self._common())
        return self._admin

    def _consumer(self, group: str) -> Consumer:
        consumer = self._consumers.get(group)
        if consumer is None:
            from confluent_kafka import Consumer

            consumer = Consumer(
                {
                    **self._common(),
                    "group.id": group,
                    "enable.auto.commit": False,
                    "enable.auto.offset.store": False,
                    "auto.offset.reset": "earliest",
                }
            )
            self._consumers[group] = consumer
        return consumer

    # ------------------------------------------------------------ KafkaLog

    def ensure_topic(self, topic: str, partitions: int, config: Mapping[str, str]) -> int:
        from confluent_kafka import KafkaError, KafkaException
        from confluent_kafka.cimpl import NewTopic

        admin = self._get_admin()
        with _reaching(f"look for topic {topic}"):
            found = admin.list_topics(topic=topic, timeout=self.timeout_s).topics.get(topic)
        if found is not None and found.error is None and found.partitions:
            return len(found.partitions)
        new = NewTopic(topic, num_partitions=partitions, replication_factor=1, config=dict(config))
        futures = admin.create_topics([new], operation_timeout=self.timeout_s)
        try:
            with _timing_out(f"create topic {topic}"):
                futures[topic].result(timeout=self.timeout_s)
        except KafkaException as exc:
            error = exc.args[0] if exc.args else None
            if not (
                isinstance(error, KafkaError) and error.code() == KafkaError.TOPIC_ALREADY_EXISTS
            ):
                raise InfrastructureError(f"kafka: create topic {topic}: {exc}") from exc
        return partitions

    def produce(self, topic: str, partition: int, key: bytes, value: bytes) -> int:
        producer = self._get_producer()
        outcome: list[tuple[Any, Any]] = []
        with _reaching(f"publish to {topic}"):
            producer.produce(
                topic,
                value=value,
                key=key,
                partition=partition,
                on_delivery=lambda err, msg: outcome.append((err, msg)),
            )
            producer.flush(self.timeout_s)
        if not outcome:
            raise InfrastructureError(
                f"kafka: {topic} did not acknowledge within {self.timeout_s}s"
            )
        error, message = outcome[0]
        if error is not None:
            raise InfrastructureError(f"kafka: publish to {topic}: {error}")
        offset: int = message.offset()
        return offset

    def read(
        self, group: str, topic: str, starts: Mapping[int, int], ends: Mapping[int, int]
    ) -> list[Record]:
        from confluent_kafka import KafkaError, TopicPartition

        wanted = {p: (start, ends[p]) for p, start in starts.items() if ends[p] > start}
        if not wanted:
            return []
        consumer = self._consumer(group)
        found: dict[int, list[Record]] = {p: [] for p in wanted}
        following = {p: start for p, (start, _) in wanted.items()}
        deadline = time.monotonic() + self.timeout_s
        with _reaching(f"read {topic}"):
            consumer.assign([TopicPartition(topic, p, start) for p, (start, _) in wanted.items()])
            while any(following[p] < end for p, (_, end) in wanted.items()):
                if time.monotonic() > deadline:
                    raise InfrastructureError(f"kafka: reading {topic} took over {self.timeout_s}s")
                for message in consumer.consume(num_messages=500, timeout=0.5):
                    error = message.error()
                    if error is not None:
                        if error.code() == KafkaError._PARTITION_EOF:
                            continue
                        raise InfrastructureError(f"kafka: read {topic}: {error}")
                    partition, offset = message.partition(), message.offset()
                    if partition is None or offset is None or partition not in wanted:
                        continue
                    if not following[partition] <= offset < wanted[partition][1]:
                        continue  # read ahead of the range, or a record seen already
                    _, stamp = message.timestamp()
                    found[partition].append(
                        Record(partition, offset, int(stamp), bytes(message.value() or b""))
                    )
                    following[partition] = offset + 1
            consumer.unassign()
        return [record for p in sorted(found) for record in found[p]]

    def watermarks(self, topic: str, partitions: int) -> dict[int, tuple[int, int]]:
        from confluent_kafka import KafkaError, KafkaException, TopicPartition

        consumer = self._consumer(WATERMARK_GROUP)
        marks: dict[int, tuple[int, int]] = {}
        with _reaching(f"look at {topic}"):
            for p in range(partitions):
                partition = TopicPartition(topic, p)
                deadline = time.monotonic() + self.timeout_s
                while True:
                    try:
                        low, high = consumer.get_watermark_offsets(
                            partition, timeout=max(0.0, deadline - time.monotonic()), cached=False
                        )
                        break
                    except KafkaException as exc:
                        # Creation is acknowledged before every leader serves offsets.
                        if (
                            exc.args[0].code()
                            not in {
                                KafkaError.NOT_LEADER_FOR_PARTITION,
                                KafkaError.LEADER_NOT_AVAILABLE,
                                KafkaError.UNKNOWN_TOPIC_OR_PART,
                            }
                            or time.monotonic() >= deadline
                        ):
                            raise
                        time.sleep(0.05)
                marks[p] = (int(low), int(high))
        return marks

    def committed(self, group: str, topic: str, partitions: int) -> dict[int, int | None]:
        from confluent_kafka import TopicPartition

        with _reaching(f"read {group}'s position in {topic}"):
            found = self._consumer(group).committed(
                [TopicPartition(topic, p) for p in range(partitions)], timeout=self.timeout_s
            )
        return {tp.partition: (tp.offset if tp.offset >= 0 else None) for tp in found}

    def commit(self, group: str, topic: str, partition: int, next_offset: int) -> None:
        from confluent_kafka import TopicPartition

        with _reaching(f"commit {group} in {topic}"):
            stored = self._consumer(group).commit(
                offsets=[TopicPartition(topic, partition, next_offset)], asynchronous=False
            )
        for tp in stored or ():
            if tp.error is not None:
                raise InfrastructureError(f"kafka: commit {group} in {topic}: {tp.error}")

    def groups(self, topic: str) -> dict[str, dict[int, int]]:
        from confluent_kafka import ConsumerGroupTopicPartitions

        admin = self._get_admin()
        positions: dict[str, dict[int, int]] = {}
        with _reaching("list consumer groups"):
            listing = admin.list_consumer_groups(request_timeout=self.timeout_s).result(
                timeout=self.timeout_s
            )
            for group in listing.valid:
                name = group.group_id
                if name == WATERMARK_GROUP:
                    continue
                futures = admin.list_consumer_group_offsets(
                    [ConsumerGroupTopicPartitions(name)], request_timeout=self.timeout_s
                )
                result = futures[name].result(timeout=self.timeout_s)
                offsets = {
                    tp.partition: tp.offset
                    for tp in result.topic_partitions or ()
                    if tp.topic == topic and tp.offset >= 0
                }
                if offsets:
                    positions[name] = offsets
        return positions

    def delete_topic(self, topic: str) -> None:
        from confluent_kafka import KafkaError, KafkaException

        futures = self._get_admin().delete_topics([topic], operation_timeout=self.timeout_s)
        try:
            with _timing_out(f"delete topic {topic}"):
                futures[topic].result(timeout=self.timeout_s)
        except KafkaException as exc:
            error = exc.args[0] if exc.args else None
            missing = isinstance(error, KafkaError) and error.code() in {
                KafkaError.UNKNOWN_TOPIC_OR_PART,
                KafkaError._UNKNOWN_TOPIC,
            }
            if not missing:
                raise InfrastructureError(f"kafka: delete topic {topic}: {exc}") from exc

    def close(self) -> None:
        for consumer in self._consumers.values():
            consumer.close()
        self._consumers.clear()
        if self._producer is not None:
            self._producer.flush(self.timeout_s)
