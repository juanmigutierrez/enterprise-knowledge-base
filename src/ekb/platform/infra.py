"""Where the real infrastructure is (S17), and the namespace every name in it starts with.

**Endpoints** come from environment variables, with defaults for running on the
host against ``docker compose up`` (the ports ``compose.yaml`` publishes on
``127.0.0.1``). Inside Compose, the services set them to the service names
(``kafka:9092``, ``http://dynamodb:8000`` …). They are not secrets, but they
differ by where a process runs, so they are not in ``config/``.

==============  =========================  ==========================
service         variable                   default (from the host)
==============  =========================  ==========================
Kafka           ``EKB_KAFKA_BOOTSTRAP``    ``127.0.0.1:29092``
DynamoDB Local  ``EKB_DYNAMODB_URL``       ``http://localhost:8000``
S3 (SeaweedFS)  ``EKB_S3_URL``             ``http://localhost:8333``
OpenSearch      ``EKB_OPENSEARCH_URL``     ``http://localhost:9200``
==============  =========================  ==========================

**The namespace.** Every name the real options create (the topic, the tables,
the bucket, the index) is :func:`scoped`: as written (``ekb.changes``,
``ekb-registry``) in the default namespace ``ekb``, and prefixed in any other
(``demo-1a2b3c4d-ekb-registry``). The services use the default one; each demo
or test run on Docker takes a fresh namespace (:func:`new_namespace`) and drops
it at the end, so runs never see each other's data. The namespace comes from a
``namespace`` parameter, else ``EKB_NAMESPACE``, else ``ekb``.
"""

from __future__ import annotations

import os
import re
import secrets

DEFAULT_NAMESPACE = "ekb"
NAMESPACE_ENV = "EKB_NAMESPACE"
_NAMESPACE = re.compile(r"[a-z0-9][a-z0-9-]{0,30}")
"""Lower case letters, digits and hyphens: valid in a Kafka topic, a DynamoDB table, an
S3 bucket and an OpenSearch index name alike."""

ENDPOINTS: dict[str, tuple[str, str]] = {
    "kafka": ("EKB_KAFKA_BOOTSTRAP", "127.0.0.1:29092"),
    "dynamodb": ("EKB_DYNAMODB_URL", "http://localhost:8000"),
    "s3": ("EKB_S3_URL", "http://localhost:8333"),
    "opensearch": ("EKB_OPENSEARCH_URL", "http://localhost:9200"),
}
"""Service -> (environment variable, default from the host)."""


START_HINT = "start it first: make up (or docker compose up -d --wait)"
"""What to say when the infrastructure does not answer."""


class InfrastructureError(ConnectionError):
    """A real infrastructure service did not answer, or refused a call.

    A ``ConnectionError`` (so an ``OSError``): the sync loops of ``ekb api``,
    ``ekb connect`` and ``ekb worker`` log it and try again next round, and
    the event that was being handled stays pending on the bus.
    """


def endpoint(service: str, given: str | None = None) -> str:
    """The address of ``service``: ``given``, else its environment variable, else the
    default for running on the host."""
    if given:
        return given
    variable, default = ENDPOINTS[service]
    return os.environ.get(variable) or default


def namespace(given: str | None = None) -> str:
    """The namespace to use: ``given``, else ``EKB_NAMESPACE``, else ``ekb``."""
    chosen = given or os.environ.get(NAMESPACE_ENV) or DEFAULT_NAMESPACE
    if not _NAMESPACE.fullmatch(chosen):
        raise ValueError(
            f"namespace {chosen!r}: use 1-31 lower case letters, digits and hyphens, "
            "starting with a letter or a digit"
        )
    return chosen


def scoped(name: str, given: str | None = None) -> str:
    """``name`` in the namespace: unchanged in ``ekb``, prefixed in any other."""
    chosen = namespace(given)
    return name if chosen == DEFAULT_NAMESPACE else f"{chosen}-{name}"


def new_namespace(prefix: str = "run") -> str:
    """A fresh namespace for one demo or test run, e.g. ``demo-1a2b3c4d``."""
    return namespace(f"{prefix}-{secrets.token_hex(4)}")
