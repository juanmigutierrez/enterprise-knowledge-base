"""A Kafka log in memory: :class:`~ekb.changelog.bus.kafka.KafkaLog` without a broker.

It answers the adapter's operations the way Kafka does (offsets per partition from
0, a committed offset per group and partition, watermarks), so the Kafka bus's own
logic (the port's promises) is tested in every pull request. The real broker is
tested by the integration tests (``-m integration``, after ``make up``).
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping

from ekb.changelog.bus.kafka import Record
from ekb.platform.infra import InfrastructureError


class FakeKafka:
    def __init__(self) -> None:
        self.topics: dict[str, list[list[Record]]] = {}
        self.configs: dict[str, dict[str, str]] = {}
        self.offsets: dict[tuple[str, str], dict[int, int]] = {}  # (group, topic) -> p -> next
        self.down = False
        """Set to make every call fail as if the broker could not be reached."""
        self.closed = 0
        self._clock = itertools.count(1_700_000_000_000)

    def _reach(self) -> None:
        if self.down:
            raise InfrastructureError("kafka: the broker cannot be reached")

    def ensure_topic(self, topic: str, partitions: int, config: Mapping[str, str]) -> int:
        self._reach()
        if topic not in self.topics:
            self.topics[topic] = [[] for _ in range(partitions)]
            self.configs[topic] = dict(config)
        return len(self.topics[topic])

    def produce(self, topic: str, partition: int, key: bytes, value: bytes) -> int:
        self._reach()
        log = self.topics[topic][partition]
        log.append(Record(partition, len(log), next(self._clock), value))
        return len(log) - 1

    def read(
        self, group: str, topic: str, starts: Mapping[int, int], ends: Mapping[int, int]
    ) -> list[Record]:
        self._reach()
        logs = self.topics[topic]
        return [r for p, start in sorted(starts.items()) for r in logs[p][start : ends[p]]]

    def watermarks(self, topic: str, partitions: int) -> dict[int, tuple[int, int]]:
        self._reach()
        return {p: (0, len(self.topics[topic][p])) for p in range(partitions)}

    def committed(self, group: str, topic: str, partitions: int) -> dict[int, int | None]:
        self._reach()
        done = self.offsets.get((group, topic), {})
        return {p: done.get(p) for p in range(partitions)}

    def commit(self, group: str, topic: str, partition: int, next_offset: int) -> None:
        self._reach()
        self.offsets.setdefault((group, topic), {})[partition] = next_offset

    def groups(self, topic: str) -> dict[str, dict[int, int]]:
        self._reach()
        return {g: dict(done) for (g, t), done in self.offsets.items() if t == topic and done}

    def delete_topic(self, topic: str) -> None:
        self.topics.pop(topic, None)
        self.offsets = {k: v for k, v in self.offsets.items() if k[1] != topic}

    def close(self) -> None:
        self.closed += 1
