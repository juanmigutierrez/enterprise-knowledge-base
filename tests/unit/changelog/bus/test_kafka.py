"""The Kafka bus (S17): what is Kafka-specific, beyond the port's promises
(``tests/contract/test_bus.py``), and the confluent-kafka adapter, driven with
stand-in client classes so its loops and its error handling run without a broker."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import confluent_kafka
import confluent_kafka.admin
import pytest
from confluent_kafka import KafkaError, KafkaException, TopicPartition

from ekb.changelog.bus.kafka import WATERMARK_GROUP, ConfluentLog
from ekb.changelog.bus.options.kafka import TOPIC_CONFIG, KafkaEventBus
from ekb.platform.infra import InfrastructureError
from tests.fakes.kafka import FakeKafka
from tests.unit.changelog.factories import delete, upsert

# ------------------------------------------------------------------ the bus on a fake log


def test_the_topic_is_created_once_in_the_namespace_and_keeps_everything() -> None:
    log = FakeKafka()
    bus = KafkaEventBus(namespace="demo-1", log=log)
    bus.publish(upsert("HR-PL-04"))
    bus.publish(upsert("HR-PL-05"))
    assert list(log.topics) == ["demo-1-ekb.changes"]
    assert log.configs["demo-1-ekb.changes"] == TOPIC_CONFIG
    assert log.configs["demo-1-ekb.changes"]["retention.ms"] == "-1"
    assert KafkaEventBus(log=FakeKafka()).topic == "ekb.changes"


def test_a_topic_with_another_partition_count_is_refused() -> None:
    log = FakeKafka()
    KafkaEventBus(partitions=3, log=log).publish(upsert("HR-PL-04"))
    with pytest.raises(ValueError, match="a document's partition would change"):
        KafkaEventBus(partitions=4, log=log).publish(upsert("HR-PL-04"))


def test_a_broker_that_cannot_be_reached_fails_loudly_and_loses_nothing() -> None:
    log = FakeKafka()
    bus = KafkaEventBus(log=log)
    bus.publish(upsert("HR-PL-04"))
    log.down = True
    with pytest.raises(InfrastructureError):  # a ConnectionError: the sync loop retries
        bus.publish(delete("HR-PL-04", "1.1"))
    with pytest.raises(ConnectionError):
        bus.poll("worker")
    log.down = False
    assert [d.event.version for d in bus.poll("worker")] == ["1.1"]


def test_stats_include_groups_another_process_committed() -> None:
    log = FakeKafka()
    worker = KafkaEventBus(partitions=1, log=log)
    for page in ("HR-PL-04", "HR-PL-05"):
        worker.publish(upsert(page))
    worker.commit("worker", worker.poll("worker")[0])
    inspector = KafkaEventBus(partitions=1, log=log)  # another process
    assert {g.group: g.lag for g in inspector.stats().groups} == {"worker": 1}


def test_destroy_deletes_the_topic_and_closes_the_clients() -> None:
    log = FakeKafka()
    bus = KafkaEventBus(namespace="test-x", log=log)
    bus.publish(upsert("HR-PL-04"))
    bus.destroy()
    assert log.topics == {}
    assert log.closed == 1


# ------------------------------------------------------------------ the confluent-kafka adapter


@dataclass
class Broker:
    """What the stand-in clients share: the topics, the committed offsets, the faults."""

    topics: dict[str, list[list[tuple[bytes, int]]]] = field(default_factory=dict)
    committed: dict[tuple[str, str, int], int] = field(default_factory=dict)
    leader_pending: bool = False
    unreachable: bool = False
    drop_acks: bool = False
    fail_acks: bool = False
    eof_first: bool = False
    stall: bool = False
    commit_error: bool = False
    clock: Iterator[int] = field(default_factory=lambda: itertools.count(1000))

    def check(self) -> None:
        if self.unreachable:
            raise KafkaException(KafkaError(KafkaError._TRANSPORT))


class Future:
    def __init__(self, value: Any = None, error: KafkaError | None = None) -> None:
        self.value, self.error = value, error

    def result(self, timeout: float | None = None) -> Any:
        if self.error is not None:
            raise KafkaException(self.error)
        return self.value


class Message:
    def __init__(
        self, partition: int, offset: int, value: bytes, stamp: int, error: Any = None
    ) -> None:
        self._p, self._o, self._v, self._t, self._e = partition, offset, value, stamp, error

    def error(self) -> Any:
        return self._e

    def partition(self) -> int:
        return self._p

    def offset(self) -> int:
        return self._o

    def value(self) -> bytes:
        return self._v

    def timestamp(self) -> tuple[int, int]:
        return (1, self._t)


def clients(broker: Broker) -> tuple[type, type, type]:
    class Producer:
        def __init__(self, config: dict[str, Any]) -> None:
            assert config["enable.idempotence"]
            assert config["acks"] == "all"
            self.pending: list[Callable[[], None]] = []

        def produce(self, topic: str, **kw: Any) -> None:
            broker.check()
            log = broker.topics[topic][kw["partition"]]
            log.append((kw["value"], next(broker.clock)))
            offset = len(log) - 1
            error = KafkaError(KafkaError._MSG_TIMED_OUT) if broker.fail_acks else None
            message = Message(kw["partition"], offset, kw["value"], 0)
            self.pending.append(lambda: kw["on_delivery"](error, message))

        def flush(self, timeout: float) -> int:
            if not broker.drop_acks:
                for done in self.pending:
                    done()
            self.pending.clear()
            return 0

    class Consumer:
        def __init__(self, config: dict[str, Any]) -> None:
            assert config["enable.auto.commit"] is False
            self.group = config["group.id"]
            self.at: dict[tuple[str, int], int] = {}
            self.closed = False
            self.eof_sent = False

        def assign(self, partitions: list[TopicPartition]) -> None:
            self.at = {(tp.topic, tp.partition): tp.offset for tp in partitions}

        def unassign(self) -> None:
            self.at = {}

        def consume(self, num_messages: int, timeout: float) -> list[Message]:
            broker.check()
            if broker.stall:
                return []
            out: list[Message] = []
            if broker.eof_first and not self.eof_sent:
                self.eof_sent = True
                out.append(Message(0, -1, b"", 0, KafkaError(KafkaError._PARTITION_EOF)))
            for (topic, p), offset in list(self.at.items()):
                log = broker.topics[topic][p]
                if offset > 0:  # a record before the range, read ahead earlier
                    out.append(Message(p, offset - 1, log[offset - 1][0], log[offset - 1][1]))
                for o in range(offset, len(log)):
                    out.append(Message(p, o, log[o][0], log[o][1]))
                self.at[(topic, p)] = len(log)
            return out[:num_messages]

        def get_watermark_offsets(
            self, tp: TopicPartition, timeout: float, cached: bool
        ) -> tuple[int, int]:
            if broker.leader_pending:
                broker.leader_pending = False
                raise KafkaException(KafkaError(KafkaError.NOT_LEADER_FOR_PARTITION))
            broker.check()
            assert self.group == WATERMARK_GROUP
            assert not cached
            return (0, len(broker.topics[tp.topic][tp.partition]))

        def committed(self, partitions: list[TopicPartition], timeout: float) -> list[Any]:
            broker.check()
            return [
                TopicPartition(
                    tp.topic,
                    tp.partition,
                    broker.committed.get((self.group, tp.topic, tp.partition), -1001),
                )
                for tp in partitions
            ]

        def commit(self, offsets: list[TopicPartition], asynchronous: bool) -> list[Any]:
            assert asynchronous is False
            stored = []
            for tp in offsets:
                broker.committed[(self.group, tp.topic, tp.partition)] = tp.offset
                stored.append(tp)
            if broker.commit_error:
                return [type("Failed", (), {"error": KafkaError(KafkaError._TIMED_OUT)})()]
            return stored

        def close(self) -> None:
            self.closed = True

    class Admin:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config

        def list_topics(self, topic: str, timeout: float) -> Any:
            broker.check()
            found = broker.topics.get(topic)
            meta = (
                {
                    topic: type(
                        "T", (), {"error": None, "partitions": dict.fromkeys(range(len(found)))}
                    )()
                }
                if found is not None
                else {}
            )
            return type("M", (), {"topics": meta})()

        def create_topics(self, topics: list[Any], operation_timeout: float) -> dict[str, Future]:
            futures = {}
            for new in topics:
                if new.topic in broker.topics:
                    futures[new.topic] = Future(error=KafkaError(KafkaError.TOPIC_ALREADY_EXISTS))
                else:
                    broker.topics[new.topic] = [[] for _ in range(new.num_partitions)]
                    futures[new.topic] = Future()
            return futures

        def delete_topics(self, topics: list[str], operation_timeout: float) -> dict[str, Future]:
            futures = {}
            for name in topics:
                if broker.topics.pop(name, None) is None:
                    futures[name] = Future(error=KafkaError(KafkaError.UNKNOWN_TOPIC_OR_PART))
                else:
                    futures[name] = Future()
            return futures

        def list_consumer_groups(self, request_timeout: float) -> Future:
            broker.check()
            names = sorted({g for g, _, _ in broker.committed} | {WATERMARK_GROUP})
            listings = [type("G", (), {"group_id": name})() for name in names]
            return Future(type("R", (), {"valid": listings})())

        def list_consumer_group_offsets(
            self, groups: list[Any], request_timeout: float
        ) -> dict[str, Future]:
            name = groups[0].group_id
            tps = [
                TopicPartition(topic, p, offset)
                for (g, topic, p), offset in broker.committed.items()
                if g == name
            ]
            return {name: Future(type("R", (), {"topic_partitions": tps})())}

    return Producer, Consumer, Admin


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch) -> Broker:
    shared = Broker()
    producer, consumer, admin = clients(shared)
    monkeypatch.setattr(confluent_kafka, "Producer", producer)
    monkeypatch.setattr(confluent_kafka, "Consumer", consumer)
    monkeypatch.setattr(confluent_kafka.admin, "AdminClient", admin)
    return shared


def test_the_bus_runs_on_the_adapter_end_to_end(broker: Broker) -> None:
    bus = KafkaEventBus(partitions=2, log=ConfluentLog("kafka:9092", timeout_s=0.5))
    first = bus.publish(upsert("HR-PL-04"))
    bus.publish(upsert("HR-PL-04", "2.1"))
    polled = bus.poll("worker")
    assert [d.event.version for d in polled] == ["1.1", "2.1"]
    bus.commit("worker", first)
    assert broker.committed[("worker", "ekb.changes", first.partition)] == 1
    other = KafkaEventBus(partitions=2, log=ConfluentLog("kafka:9092", timeout_s=0.5))
    assert [d.event.version for d in other.poll("worker")] == ["2.1"]  # resumes
    assert {g.group: g.lag for g in other.stats().groups} == {"worker": 1}
    other.destroy()
    assert "ekb.changes" not in broker.topics


def test_an_existing_topic_is_found_and_a_race_to_create_it_is_harmless(broker: Broker) -> None:
    log = ConfluentLog("kafka:9092")
    assert log.ensure_topic("t", 3, {}) == 3
    assert log.ensure_topic("t", 5, {}) == 3  # found: its own count
    broker.topics.pop("t")
    real_list = confluent_kafka.admin.AdminClient.list_topics

    def stale(self: Any, topic: str, timeout: float) -> Any:  # another process just created it
        broker.topics[topic] = [[]]
        result = real_list(self, "missing", timeout)
        return result

    confluent_kafka.admin.AdminClient.list_topics = stale  # type: ignore[method-assign]
    try:
        assert ConfluentLog("kafka:9092").ensure_topic("t", 1, {}) == 1
    finally:
        confluent_kafka.admin.AdminClient.list_topics = real_list  # type: ignore[method-assign]


@pytest.mark.parametrize("fault", ["unreachable", "drop_acks", "fail_acks"])
def test_a_publish_kafka_did_not_acknowledge_is_an_infrastructure_error(
    broker: Broker, fault: str
) -> None:
    log = ConfluentLog("kafka:9092", timeout_s=0.1)
    log.ensure_topic("t", 1, {})
    setattr(broker, fault, True)
    with pytest.raises(InfrastructureError, match="kafka"):
        log.produce("t", 0, b"k", b"v")


def test_read_returns_exactly_the_range_and_skips_end_of_partition_notices(broker: Broker) -> None:
    log = ConfluentLog("kafka:9092", timeout_s=1.0)
    log.ensure_topic("t", 2, {})
    for value in (b"a", b"b", b"c", b"d"):
        log.produce("t", 0, b"k", value)
    log.produce("t", 1, b"k", b"x")
    broker.eof_first = True
    records = log.read("worker", "t", {0: 1, 1: 0}, {0: 3, 1: 1})
    assert [(r.partition, r.offset, r.value) for r in records] == [
        (0, 1, b"b"),
        (0, 2, b"c"),
        (1, 0, b"x"),
    ]
    assert log.read("worker", "t", {0: 2}, {0: 2}) == []


def test_a_read_that_never_completes_times_out(broker: Broker) -> None:
    log = ConfluentLog("kafka:9092", timeout_s=0.2)
    log.ensure_topic("t", 1, {})
    log.produce("t", 0, b"k", b"a")
    broker.stall = True
    with pytest.raises(InfrastructureError, match="took over"):
        log.read("worker", "t", {0: 0}, {0: 1})


def test_offsets_watermarks_and_groups(broker: Broker) -> None:
    log = ConfluentLog("kafka:9092")
    log.ensure_topic("t", 2, {})
    log.ensure_topic("other", 1, {})
    log.produce("t", 1, b"k", b"a")
    broker.leader_pending = True
    assert log.watermarks("t", 2) == {0: (0, 0), 1: (0, 1)}
    assert log.committed("worker", "t", 2) == {0: None, 1: None}
    log.commit("worker", "t", 1, 1)
    log.commit("worker", "other", 0, 0)
    assert log.committed("worker", "t", 2) == {0: None, 1: 1}
    assert log.groups("t") == {"worker": {1: 1}}
    broker.commit_error = True
    with pytest.raises(InfrastructureError, match="commit"):
        log.commit("worker", "t", 1, 1)


def test_deleting_a_missing_topic_is_harmless_and_close_closes_every_consumer(
    broker: Broker,
) -> None:
    log = ConfluentLog("kafka:9092")
    log.delete_topic("never-created")
    log.ensure_topic("t", 1, {})
    log.produce("t", 0, b"k", b"a")
    log.committed("worker", "t", 1)
    log.close()
    log.close()  # twice is fine


def test_kafkas_own_errors_become_infrastructure_errors(broker: Broker) -> None:
    broker.unreachable = True
    log = ConfluentLog("kafka:9092")
    for call in (
        lambda: log.ensure_topic("t", 1, {}),
        lambda: log.watermarks("t", 1),
        lambda: log.committed("g", "t", 1),
        lambda: log.groups("t"),
    ):
        with pytest.raises(InfrastructureError):
            call()


def test_an_admin_request_that_never_answers_is_an_infrastructure_error(
    broker: Broker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its future raises a bare TimeoutError (an OSError with no message): the adapter
    names what timed out instead."""

    class Silent(Future):
        def result(self, timeout: float | None = None) -> Any:
            raise TimeoutError

    def silent(self: Any, topics: list[str], operation_timeout: float) -> dict[str, Future]:
        return {topic: Silent() for topic in topics}

    monkeypatch.setattr(confluent_kafka.admin.AdminClient, "delete_topics", silent)
    with pytest.raises(InfrastructureError, match="delete topic t: no answer in time"):
        ConfluentLog("kafka:9092").delete_topic("t")
