"""boto3 behind two small helpers: a DynamoDB :class:`Table` and an S3 client (S17).

This is the only module that imports boto3 or botocore (an import-linter
contract), and it imports them only when a client is built, so ``ekb`` runs
without the ``aws`` extra until a DynamoDB or S3 option is chosen.

**Locally** the endpoints are DynamoDB Local and SeaweedFS's S3 API
(:mod:`ekb.platform.infra`). Both accept any credentials, but boto3 must sign
every request with some, so when an endpoint is set and no AWS credentials are
in the environment, the placeholder :data:`EMULATOR_CREDENTIALS` signs them.
They open nothing: they are not a secret. On AWS (an empty endpoint) boto3's
own credential chain is used, as everywhere else in production.

**How the options store a model.** An item holds its key, the few attributes a
query or a filter needs (``source``, ``source_key``, …) and the whole model as
JSON in ``doc``. Reading it back is ``Model.model_validate_json(item["doc"])``:
the same validation as everywhere else, and no second schema to keep in step.

**Reads are strongly consistent** (``ConsistentRead``), so a process sees what
another one has just written: the permission and deletion gates depend on it.
That is also why no option uses a secondary index: an index cannot be read that
way, and a purge that looked up a leaver's membership in a stale index could
miss it. Where a store must find an item by something other than its key (the
record that defined it), it keeps a second small table for that.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any, Protocol

from ekb.platform.infra import InfrastructureError, endpoint

EMULATOR_CREDENTIALS = ("ekblocal", "ekb-local")
"""Placeholder access key and secret for DynamoDB Local and SeaweedFS, which check neither."""
DEFAULT_REGION = "eu-west-1"
TABLE_WAIT_S = 20.0

Value = str | int | bool


class DynamoDBClient(Protocol):
    """The DynamoDB calls the options make (boto3's low-level client has them all)."""

    def get_item(self, **kwargs: Any) -> dict[str, Any]: ...
    def put_item(self, **kwargs: Any) -> dict[str, Any]: ...
    def delete_item(self, **kwargs: Any) -> dict[str, Any]: ...
    def scan(self, **kwargs: Any) -> dict[str, Any]: ...
    def query(self, **kwargs: Any) -> dict[str, Any]: ...
    def create_table(self, **kwargs: Any) -> dict[str, Any]: ...
    def describe_table(self, **kwargs: Any) -> dict[str, Any]: ...
    def delete_table(self, **kwargs: Any) -> dict[str, Any]: ...


class S3Client(Protocol):
    """The S3 calls the raw store makes."""

    def head_bucket(self, **kwargs: Any) -> dict[str, Any]: ...
    def create_bucket(self, **kwargs: Any) -> dict[str, Any]: ...
    def delete_bucket(self, **kwargs: Any) -> dict[str, Any]: ...
    def put_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def delete_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]: ...


def _session_kwargs(url: str | None) -> dict[str, Any]:
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or DEFAULT_REGION
    kwargs: dict[str, Any] = {"region_name": region}
    if url:
        kwargs["endpoint_url"] = url
        if not os.environ.get("AWS_ACCESS_KEY_ID"):
            kwargs["aws_access_key_id"], kwargs["aws_secret_access_key"] = EMULATOR_CREDENTIALS
    return kwargs


def dynamodb_client(url: str | None = None) -> DynamoDBClient:
    """A DynamoDB client for ``url`` (default: ``EKB_DYNAMODB_URL``; empty: AWS itself)."""
    import boto3  # the aws extra, only when a DynamoDB option is built
    from botocore.config import Config

    target = endpoint("dynamodb", url) or None
    config = Config(connect_timeout=2, read_timeout=5, retries={"max_attempts": 2})
    client: DynamoDBClient = boto3.client("dynamodb", config=config, **_session_kwargs(target))
    return client


def s3_client(url: str | None = None) -> S3Client:
    """An S3 client for ``url`` (default: ``EKB_S3_URL``; empty: AWS itself). Path-style
    addresses (``http://host/bucket/key``), which SeaweedFS needs."""
    import boto3  # the aws extra, only when the S3 option is built
    from botocore.config import Config

    target = endpoint("s3", url) or None
    config = Config(
        connect_timeout=2,
        read_timeout=10,
        retries={"max_attempts": 2},
        s3={"addressing_style": "path"},
    )
    client: S3Client = boto3.client("s3", config=config, **_session_kwargs(target))
    return client


class AwsError(Exception):
    """An AWS service answered with an error code (a missing table, a missing object …).

    The options catch this, not botocore's ``ClientError``, so only this module
    imports botocore. ``code`` is AWS's error code (``ResourceNotFoundException``,
    ``NoSuchKey``, ``404`` …).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def _error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        error = response.get("Error", {})
        if isinstance(error, Mapping):
            return str(error.get("Code", ""))
    return ""


def call[T](what: str, fn: Callable[[], T]) -> T:
    """Run one AWS call. A service that cannot be reached raises
    :class:`~ekb.platform.infra.InfrastructureError` (retry later); an answer with an
    error code raises :class:`AwsError` for the caller, which knows what it means."""
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        return fn()
    except ClientError as exc:
        raise AwsError(_error_code(exc), f"{what}: {exc}") from exc
    except BotoCoreError as exc:  # connection refused, timeouts, closed connections
        raise InfrastructureError(f"{what}: {exc}") from exc


# ------------------------------------------------------------------ DynamoDB items


def encode(attributes: Mapping[str, Value]) -> dict[str, dict[str, Any]]:
    """Plain values -> DynamoDB's typed attributes (``{"S": …}``, ``{"N": …}``, ``{"BOOL": …}``)."""
    item: dict[str, dict[str, Any]] = {}
    for name, value in attributes.items():
        if isinstance(value, bool):
            item[name] = {"BOOL": value}
        elif isinstance(value, int):
            item[name] = {"N": str(value)}
        else:
            item[name] = {"S": value}
    return item


def decode(item: Mapping[str, Mapping[str, Any]]) -> dict[str, Value]:
    """DynamoDB's typed attributes -> plain values (the inverse of :func:`encode`)."""
    plain: dict[str, Value] = {}
    for name, typed in item.items():
        if "S" in typed:
            plain[name] = str(typed["S"])
        elif "N" in typed:
            plain[name] = int(typed["N"])
        elif "BOOL" in typed:
            plain[name] = bool(typed["BOOL"])
    return plain


class Table:
    """One DynamoDB table with a string partition key, created on first use. Every read is
    strongly consistent."""

    def __init__(self, client: DynamoDBClient, name: str, key: str) -> None:
        self.client, self.name, self.key = client, name, key
        self._ready = False

    def ensure(self) -> None:
        """Create the table if it does not exist yet, and wait until it is active."""
        if self._ready:
            return
        try:
            status = call(
                f"describe {self.name}", lambda: self.client.describe_table(TableName=self.name)
            )["Table"]["TableStatus"]
        except AwsError as exc:
            if exc.code != "ResourceNotFoundException":
                raise
            self._create()
            status = "CREATING"
        if status != "ACTIVE":
            self._wait_active()
        self._ready = True

    def _create(self) -> None:
        request: dict[str, Any] = {
            "TableName": self.name,
            "KeySchema": [{"AttributeName": self.key, "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": self.key, "AttributeType": "S"}],
            "BillingMode": "PAY_PER_REQUEST",
        }
        try:
            call(f"create {self.name}", lambda: self.client.create_table(**request))
        except AwsError as exc:
            if exc.code != "ResourceInUseException":  # another process was first
                raise

    def _wait_active(self) -> None:
        deadline = time.monotonic() + TABLE_WAIT_S
        while time.monotonic() < deadline:
            table = call(
                f"describe {self.name}", lambda: self.client.describe_table(TableName=self.name)
            )["Table"]
            if table["TableStatus"] == "ACTIVE":
                return
            time.sleep(0.1)
        raise InfrastructureError(f"table {self.name} did not become active in {TABLE_WAIT_S}s")

    def get(self, key: str) -> dict[str, Value] | None:
        self.ensure()
        found = call(
            f"read {self.name}",
            lambda: self.client.get_item(
                TableName=self.name, Key=encode({self.key: key}), ConsistentRead=True
            ),
        ).get("Item")
        return decode(found) if found else None

    def put(self, attributes: Mapping[str, Value]) -> None:
        self.ensure()
        call(
            f"write {self.name}",
            lambda: self.client.put_item(TableName=self.name, Item=encode(attributes)),
        )

    def delete(self, key: str) -> dict[str, Value] | None:
        """Delete the item; return it as it was, or ``None`` if there was none."""
        self.ensure()
        old = call(
            f"remove an item of {self.name}",
            lambda: self.client.delete_item(
                TableName=self.name, Key=encode({self.key: key}), ReturnValues="ALL_OLD"
            ),
        ).get("Attributes")
        return decode(old) if old else None

    def scan(self) -> Iterator[dict[str, Value]]:
        """Every item, page by page (strongly consistent)."""
        self.ensure()
        request: dict[str, Any] = {"TableName": self.name, "ConsistentRead": True}
        while True:
            page = call(f"scan {self.name}", lambda: self.client.scan(**request))
            yield from (decode(item) for item in page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                return
            request["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def drop(self) -> None:
        """Delete the whole table (the end of a demo or test run); missing is fine."""
        try:
            call(f"drop {self.name}", lambda: self.client.delete_table(TableName=self.name))
        except AwsError as exc:
            if exc.code != "ResourceNotFoundException":
                raise
        self._ready = False
