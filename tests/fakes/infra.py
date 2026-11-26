"""The real options with their services faked: the whole system on Kafka, DynamoDB, S3
and OpenSearch code, in the test process (S17).

:func:`fake_infrastructure` points every real option's client at a fake: the
Kafka bus at :class:`~tests.fakes.kafka.FakeKafka`, the DynamoDB and S3 options
at moto, the OpenSearch store at :class:`~tests.fakes.opensearch.FakeOpenSearch`.
Choices made with ``docker=True`` then build the real options as usual, and the
gate tests run the S16 story through them in every pull request. The services
themselves are tested by the integration tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest

import ekb.changelog.acceptance.options.dynamodb as acceptance_dynamodb
import ekb.changelog.bus.options.kafka as bus_kafka
import ekb.changelog.rawstore.options.s3 as rawstore_s3
import ekb.changelog.registry.options.dynamodb as registry_dynamodb
import ekb.stores.entities.options.dynamodb as entities_dynamodb
import ekb.stores.permissions.options.dynamodb as permissions_dynamodb
import ekb.stores.search.options.opensearch as search_opensearch
from ekb.platform.aws import DynamoDBClient, S3Client
from tests.fakes.aws import moto_clients
from tests.fakes.kafka import FakeKafka
from tests.fakes.opensearch import FakeOpenSearch


@dataclass(frozen=True)
class FakeInfrastructure:
    kafka: FakeKafka
    opensearch: FakeOpenSearch
    dynamodb: DynamoDBClient
    s3: S3Client


@contextmanager
def fake_infrastructure(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeInfrastructure]:
    kafka, opensearch = FakeKafka(), FakeOpenSearch()
    with moto_clients(monkeypatch) as (dynamodb, s3):

        def dynamo(url: str | None = None) -> DynamoDBClient:
            return dynamodb

        def objects(url: str | None = None) -> S3Client:
            return s3

        def log(bootstrap: str, timeout_s: float = 10.0) -> Any:
            return kafka

        monkeypatch.setattr(bus_kafka, "ConfluentLog", log)
        for module in (
            registry_dynamodb,
            acceptance_dynamodb,
            entities_dynamodb,
            permissions_dynamodb,
        ):
            monkeypatch.setattr(module, "dynamodb_client", dynamo)
        monkeypatch.setattr(rawstore_s3, "s3_client", objects)
        monkeypatch.setattr(search_opensearch, "_client", lambda url: opensearch)
        yield FakeInfrastructure(kafka, opensearch, dynamodb, s3)
