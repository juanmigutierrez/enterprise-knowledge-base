"""DynamoDB and S3 in process, with moto: the boto3 options tested without Docker.

moto answers boto3's calls inside the test process. It is not DynamoDB Local or
SeaweedFS, so the integration tests run the same suites against those
(``-m integration``, after ``make up``).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import boto3
import pytest
from moto import mock_aws

from ekb.platform.aws import DynamoDBClient, S3Client

REGION = "eu-west-1"


@contextmanager
def moto_clients(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[DynamoDBClient, S3Client]]:
    """A DynamoDB and an S3 client that moto answers; nothing leaves the process."""
    for name, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",  # moto's documented placeholder
        "AWS_DEFAULT_REGION": REGION,
    }.items():
        monkeypatch.setenv(name, value)
    with mock_aws():
        yield (
            boto3.client("dynamodb", region_name=REGION),
            boto3.client("s3", region_name=REGION),
        )
