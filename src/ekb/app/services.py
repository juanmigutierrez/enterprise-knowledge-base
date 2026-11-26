"""The POC as three services on the real infrastructure (S17): connect, worker, query.

Until S17 the whole POC had to run in one process (:class:`~ekb.app.system.KnowledgeSystem`):
the change log was in memory, so the connectors, the worker and the Knowledge
API could only meet inside it. With Kafka, DynamoDB, S3 and OpenSearch they run
apart, as the architecture's containers do, and each one restarts on its own::

    mock sources ─▶ ekb connect ─▶ Kafka: ekb.changes      (the events, in order per item)
                                   DynamoDB: ekb-accepted  (lifetimes, latest events)
                                   S3: ekb-raw             (the originals)
                                        │
                                        ▼  group "worker"
                    ekb worker  ─▶ OpenSearch: ekb-chunks, DynamoDB: entities, memberships
                                        │
    agent ── MCP ─▶ ekb api --query-only ─▶ Q1 … Q4 ─▶ the HR assistant
                         └─ group "answer-cache": a delete on the bus empties the answers
                            that touched the page (S16), in this process's cache

**The same code as the one-process system.** Every service is built from the
same pieces (``build_changelog``, ``build_stores``, ``build_connectors``,
``build_pipeline``, ``build_query``, ``build_assistant``); only the wiring of
who runs what differs. A round that fails (a source or a service down) is
logged and tried again on the next one; nothing is lost, because nothing is
committed on the bus until it is done.

**What the one-process system did that needs a consumer here:** the worker
used to empty the answer cache on a purge, but the cache now lives in the API
process. So the API reads the bus too, with its own consumer group, and
forgets the answers that touched a page as soon as its delete is on the bus.
That is the "several independent consumers of one stream" Kafka is for (D4).
"""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable
from pathlib import Path
from types import FrameType

import httpx

from ekb.access.auth.port import TokenCheck
from ekb.app.answer import access_check, build_query
from ekb.app.assistant import Assistant, build_assistant, build_tokens
from ekb.app.changelog import ChangeLog
from ekb.app.choices import Choices
from ekb.app.connectors import Connectors, build_connectors
from ekb.app.infra import require_shared
from ekb.app.pipeline import Pipeline, Stores, build_changelog, build_pipeline, build_stores
from ekb.app.search import query_encoder
from ekb.app.system import RETRYABLE
from ekb.changelog.bus.consume import drain
from ekb.changelog.bus.port import Delivery
from ekb.contracts import ChangeOp
from ekb.platform.gateway.gateway import ModelGateway

logger = logging.getLogger(__name__)

ANSWER_GROUP = "answer-cache"
"""The Knowledge API's consumer group: it reads the deletes, to empty the answer cache."""


class ConnectService:
    """``ekb connect``: the six connectors, publishing to the shared change log."""

    def __init__(
        self,
        docs: httpx.Client,
        hr: httpx.Client,
        idp: httpx.Client,
        db_path: Path,
        write: Choices,
    ) -> None:
        require_shared(write, "ekb connect")
        self.changelog: ChangeLog = build_changelog(write)
        self.connectors: Connectors = build_connectors(docs, hr, idp, db_path, self.changelog)

    def round(self, *, listings: bool) -> int:
        """The frequent connectors, then (``listings``) the full listings that catch
        deletes. Returns how many events went on the bus."""
        sent = sum(connector.sync() for connector in self.connectors.frequent)
        if listings:
            sent += sum(connector.sync() for connector in self.connectors.listings)
        return sent


class WorkerService:
    """``ekb worker``: T2 … T8 on the bus, filling the shared stores."""

    def __init__(self, write: Choices, gateway: ModelGateway | None = None) -> None:
        require_shared(write, "ekb worker")
        self.changelog: ChangeLog = build_changelog(write)
        self.stores: Stores = build_stores(write, self.changelog)
        self.pipeline: Pipeline = build_pipeline(
            write, self.changelog, stores=self.stores, gateway=gateway
        )

    def round(self, *, listings: bool) -> int:
        """Drain the worker's group, then re-link what an entity change made stale; on a
        listing round, also retry the pages whose context headers failed (as S16)."""
        handled = self.pipeline.run()
        self.pipeline.relink(retry_headers=listings)
        return handled


class QueryService:
    """``ekb api --query-only``: Q1 … Q4 and the HR assistant over the shared stores."""

    def __init__(
        self,
        docs: httpx.Client,
        idp: httpx.Client,
        *,
        write: Choices,
        query: Choices,
        assistant: Choices,
        gateway: ModelGateway | None = None,
        tokens: TokenCheck | None = None,
    ) -> None:
        require_shared(write, "ekb api --query-only")
        self.write = write
        self.changelog: ChangeLog = build_changelog(write)
        self.stores: Stores = build_stores(write, self.changelog)
        self.encoder = query_encoder(write, gateway)
        self.knowledge = build_query(query, self.stores, access_check(docs, idp), self.encoder)
        self.assistant: Assistant = build_assistant(assistant, self.knowledge, gateway)
        self.tokens: TokenCheck = tokens or build_tokens(assistant)
        self.forgotten_answers = 0

    def round(self, *, listings: bool = False) -> int:
        """Read the bus with :data:`ANSWER_GROUP` and empty the answer cache of every page
        deleted since the last round. Returns how many events it read."""
        return drain(self.changelog.bus, ANSWER_GROUP, self._forget_deleted)

    def _forget_deleted(self, delivery: Delivery) -> None:
        if delivery.event.op is ChangeOp.DELETE:
            self.forgotten_answers += self.assistant.cache.forget(delivery.event.document_key)


def run_rounds(
    round_once: Callable[..., object],
    *,
    interval_s: float,
    listing_every: int,
    stop: threading.Event,
    name: str,
) -> None:
    """Call ``round_once(listings=…)`` every ``interval_s`` until ``stop``; every
    ``listing_every``-th round is a listing round. A round that fails is logged and
    the next one tries again (a source or a service may be down for a while)."""
    rounds = 0
    while not stop.is_set():
        rounds += 1
        try:
            round_once(listings=rounds % listing_every == 1 or listing_every == 1)
        except RETRYABLE as exc:
            logger.warning("%s: round %d failed, retrying next round: %s", name, rounds, exc)
        stop.wait(interval_s)


def stop_on_signals(stop: threading.Event) -> None:
    """``docker compose stop`` sends SIGTERM: finish the current round, then stop."""

    def handle(signum: int, frame: FrameType | None) -> None:
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, handle)
