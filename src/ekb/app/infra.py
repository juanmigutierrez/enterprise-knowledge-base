"""The real infrastructure, from the composition root (S17): runs, namespaces, services.

Three things every entry point on Docker needs, written once:

- **A run of its own.** A demo with ``--docker`` (or a test) works in a fresh
  namespace (:func:`run_namespace`): its own topic, tables, bucket and
  indexes, dropped at the end (:func:`drop`), so runs never see each other's
  data and the services' namespace (``ekb``) is never touched.
- **A service shares, so it cannot be in memory.** ``ekb connect``,
  ``ekb worker`` and ``ekb api --query-only`` are separate processes; what one
  writes, the others read. :func:`require_shared` refuses to start one whose
  boxes 3 and 5 would be in memory (the ``ci`` profile), instead of running a
  system whose parts cannot see each other.
- **A plain message** when the infrastructure does not answer
  (:data:`START_HINT`).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from ekb.app.choices import IN_MEMORY, INFRA_STEPS, Choices
from ekb.changelog.acceptance.options.dynamodb import DynamoAcceptanceLog
from ekb.changelog.bus.options.kafka import KafkaEventBus
from ekb.changelog.rawstore.options.s3 import S3RawStore
from ekb.changelog.registry.options.dynamodb import DynamoRegistry
from ekb.platform.infra import (
    DEFAULT_NAMESPACE,
    START_HINT,
    new_namespace,
)
from ekb.stores.entities.options.dynamodb import DynamoEntityStore
from ekb.stores.permissions.options.dynamodb import DynamoPermissionStore
from ekb.stores.search.options.opensearch import OpenSearchSearchStore

logger = logging.getLogger(__name__)

__all__ = ["START_HINT", "SharedInfrastructureError", "drop", "require_shared", "run_namespace"]


class SharedInfrastructureError(ValueError):
    """A service would keep in memory what the other services must see."""


def require_shared(choices: Choices, service: str) -> None:
    """Refuse to run ``service`` on its own if any of boxes 3 and 5 is in memory."""
    alone = [c.step for c in choices.steps if c.step in INFRA_STEPS and c.option == IN_MEMORY]
    if alone:
        raise SharedInfrastructureError(
            f"{service} runs as its own process and shares the change log and the stores "
            f"with the other services, but {', '.join(alone)} would be in memory "
            f"(profile {choices.profile}). Use the local profile, where they are Kafka, "
            "DynamoDB, S3 and OpenSearch."
        )


def drop(namespace: str) -> None:
    """Delete everything the real options made in ``namespace``: the topic, the tables,
    the bucket and the indexes. Never the services' own namespace."""
    if namespace == DEFAULT_NAMESPACE:
        raise ValueError(f"refusing to drop the services' namespace {namespace!r}")
    KafkaEventBus(namespace=namespace).destroy()
    for built in (
        DynamoRegistry(namespace=namespace),
        DynamoAcceptanceLog(namespace=namespace),
        S3RawStore(namespace=namespace),
        DynamoEntityStore(namespace=namespace),
        DynamoPermissionStore(namespace=namespace),
        OpenSearchSearchStore(namespace=namespace),
    ):
        built.destroy()


@contextmanager
def run_namespace(docker: bool, prefix: str = "demo") -> Iterator[str | None]:
    """A fresh namespace for one run on Docker (``None`` in memory), dropped at the end.
    A drop that fails (the infrastructure was never up, say) is logged, not raised: the
    run's own result, or its own error, matters more."""
    if not docker:
        yield None
        return
    namespace = new_namespace(prefix)
    try:
        yield namespace
    finally:
        try:
            drop(namespace)
        except Exception as exc:  # never hide the run's own error behind the clean-up's
            logger.warning("could not drop namespace %s: %s", namespace, exc)
