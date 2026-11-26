"""Where the behaviour suites get their options (S17): in memory, through moto, or real.

``where`` is one of:

- ``memory``: the in-memory option;
- ``moto``: the DynamoDB or S3 option, answered by moto in the test process
  (every pull request);
- ``docker``: the same option against DynamoDB Local / SeaweedFS in
  ``docker compose`` (``-m integration``, after ``make up``), each test in its own
  namespace, dropped at the end.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from ekb.platform.aws import DynamoDBClient, S3Client
from ekb.platform.infra import new_namespace

WHERE = ["memory", "moto", pytest.param("docker", marks=pytest.mark.integration)]


@dataclass
class Infra:
    """One test's place to build options: clients (moto) or a namespace (Docker)."""

    where: str
    dynamodb: DynamoDBClient | None = None
    s3: S3Client | None = None
    namespace: str = field(default_factory=lambda: new_namespace("test"))
    _drops: list[Callable[[], object]] = field(default_factory=list)

    @property
    def durable(self) -> bool:
        return self.where != "memory"

    def aws_params(self, service: str) -> dict[str, Any]:
        """What a DynamoDB (``dynamodb``) or S3 (``s3``) option is built with here."""
        if self.where == "moto":
            return {"client": self.dynamodb if service == "dynamodb" else self.s3}
        return {"namespace": self.namespace}

    def dropped_at_the_end(self, drop: Callable[[], object]) -> None:
        if self.where == "docker":
            self._drops.append(drop)
