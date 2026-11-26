"""The whole POC in one process, with the Knowledge API on top (S15).

Until the change log is durable (S17), connectors, worker and API share one
process, as in ``ekb inspector``. :class:`KnowledgeSystem` builds it, for
``ekb api`` and ``ekb demo mcp`` alike::

    4 mock sources ─▶ 6 connectors ─▶ change log ─▶ worker (T2 … T8) ─▶ stores
                                                                          ▲
    agent ── MCP + bearer token ─▶ auth ─▶ whoami · search · ask ─▶ Q1 … Q4 ┘
                                                     └─ current access check ─▶ docs tool, IdP
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import httpx
from mcp.server import MCPServer

from ekb.access.auth.options.static_tokens import IssuedToken
from ekb.access.auth.port import TokenCheck
from ekb.access.mcp.server import create_server
from ekb.app.answer import KnowledgeQuery, access_check, build_query
from ekb.app.assistant import Assistant, AssistantResult, build_assistant, build_tokens
from ekb.app.choices import Choices
from ekb.app.connectors import build_connectors
from ekb.app.pipeline import (
    EventFailedError,
    Outcome,
    build_changelog,
    build_pipeline,
    build_stores,
)
from ekb.app.search import encoder_note, query_encoder
from ekb.contracts import Caller, Trace, TraceStatus
from ekb.platform.gateway.gateway import ModelGateway

logger = logging.getLogger(__name__)

_RETRYABLE = (httpx.HTTPError, sqlite3.Error, OSError, ValueError, LookupError, EventFailedError)
"""What a sync round logs and retries next round, as in ``ekb inspector``."""
PLACEHOLDER = "<token>"
KEEP_ANSWERS = 50
KEEP_TRACES = 1000


class KnowledgeSystem:
    """The whole POC in one process: mocks' connectors, change log, worker, stores, and
    the query path and HR assistant on top, with the token check that guards them."""

    def __init__(
        self,
        docs: httpx.Client,
        hr: httpx.Client,
        idp: httpx.Client,
        db_path: Path,
        *,
        write: Choices,
        query: Choices,
        assistant: Choices,
        gateway: ModelGateway | None = None,
        tokens: TokenCheck | None = None,
        access_docs: httpx.Client | None = None,
    ) -> None:
        """``access_docs`` is the client Q4's current access check reads the docs tool
        with (default: ``docs``); the demo passes one it can switch off."""
        self.write, self.query, self.assistant_choices = write, query, assistant
        self.changelog = build_changelog(write)
        self.stores = build_stores(write, self.changelog)
        self.connectors = build_connectors(docs, hr, idp, db_path, self.changelog)
        self.encoder = query_encoder(write, gateway)
        self.knowledge: KnowledgeQuery = build_query(
            query, self.stores, access_check(access_docs or docs, idp), self.encoder
        )
        self.answers: deque[tuple[Caller, AssistantResult]] = deque(maxlen=KEEP_ANSWERS)
        """The latest ``ask`` calls and how each came about, for the demo."""
        self.assistant: Assistant = build_assistant(
            assistant, self.knowledge, gateway, on_answer=self._answered
        )
        self.forgotten_answers = 0
        """Cached answers dropped because a page they touched was deleted (S16)."""
        self.traces: deque[Trace] = deque(maxlen=KEEP_TRACES)
        """The worker's latest attempts, for the demo (they hold no content, S12)."""
        self.pipeline = build_pipeline(
            write,
            self.changelog,
            stores=self.stores,
            gateway=gateway,
            observers=(self._forget_answers,),
            on_trace=self.traces.append,
        )
        self.pipeline.purge_guard = self.assistant.release_guard
        self.tokens: TokenCheck = tokens or build_tokens(assistant)
        self._sync_lock = threading.Lock()

    def _answered(self, caller: Caller, outcome: AssistantResult) -> None:
        self.answers.append((caller, outcome))

    def _forget_answers(self, outcome: Outcome) -> None:
        """A purge also empties the answer cache of every answer that touched the page
        (S16): deleted content is not kept, not even until its next re-check. The model
        cache forgets through the gateway, in the purge itself."""
        if outcome.trace.status is TraceStatus.PURGED:
            key = outcome.trace.document_key
            self.forgotten_answers += self.assistant.cache.forget(key)

    def last_answer(self, user_id: str) -> AssistantResult | None:
        """How ``user_id``'s latest ``ask`` came about (reused, dropped and why, held back)."""
        for caller, outcome in reversed(self.answers):
            if caller.user_id == user_id:
                return outcome
        return None

    def sync(self, *, listings: bool = False) -> None:
        """One round: the frequent connectors, the worker, then (``listings``) the full
        listings that catch deletes, and the worker again. After each drain the worker
        re-links the pages an entity change affected (S16); on a listing round it also
        retries the pages whose context headers failed."""
        with self._sync_lock:
            for connector in self.connectors.frequent:
                connector.sync()
            self.pipeline.run()
            self.pipeline.relink()
            if listings:
                for connector in self.connectors.listings:
                    connector.sync()
                self.pipeline.run()
                self.pipeline.relink(retry_headers=True)

    def server(self, base_url: str) -> MCPServer:
        return create_server(self.assistant, self.tokens, base_url=base_url)

    @property
    def issued(self) -> tuple[IssuedToken, ...]:
        """The test tokens, when the token check issued them (``static_tokens``)."""
        issued = getattr(self.tokens, "issued", ())
        return tuple(t for t in issued if isinstance(t, IssuedToken))

    def describe(self) -> list[str]:
        """The options, one per line, for the startup message and the demo."""
        lines = [choice.describe() for choice in self.query.steps]
        built = {
            "auth": self.tokens.auth_id,
            "answer": self.assistant.writer.writer_id,
            "answer_cache": self.assistant.cache.cache_id,
        }
        for choice in self.assistant_choices.steps:
            named = replace(choice, params={}).describe()
            lines.append(f"{named}: {built.get(choice.step, choice.option)}")
        lines.append(encoder_note(self.write, self.encoder))
        return lines


@contextmanager
def syncing(system: KnowledgeSystem, interval_s: float, listing_every: int) -> Iterator[None]:
    """Keep the system in sync in a background thread; a failed round is logged and retried."""
    stop = threading.Event()

    def loop() -> None:
        rounds = 1
        while not stop.wait(interval_s):
            try:
                system.sync(listings=rounds % listing_every == 0)
            except _RETRYABLE as exc:
                logger.warning("sync failed, retrying next round: %s", exc)
            rounds += 1

    thread = threading.Thread(target=loop, name="knowledge-sync", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=interval_s + 5)


def connect_hint(url: str, token: str = PLACEHOLDER) -> str:
    """The command that connects Claude Code to the API with one person's token."""
    return f'claude mcp add --transport http ekb {url} --header "Authorization: Bearer {token}"'
