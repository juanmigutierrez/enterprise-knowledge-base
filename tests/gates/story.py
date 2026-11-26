"""The gate tests' world (S16): the whole POC in one process, on in-process mocks.

The four mocks, the six connectors, the change log, the worker with its re-links,
the stores, Q1 to Q4 and the HR assistant, as ``ekb api`` builds them
(:class:`~ekb.app.system.KnowledgeSystem`), offline. The answer writer quotes the
passages (``extractive``), or writes through the ``ci`` profile's fake gateway
(``model``), so a model reply is cached and the deletion gate can check that it is
forgotten. Q4 reads the docs tool through a switch that can take
it down (the timeout case of card 4.13).

Every gate runs on the ``ci`` and ``local`` profiles, in memory, and (S17) on the
real options for boxes 3 and 5: over fakes in every pull request, and against
``docker compose`` with ``-m integration``. The gates hold whatever the options
are. Every agent-facing reply is kept (:attr:`Story.replies`), so a test can scan
everything an agent was ever told.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi.testclient import TestClient
from sources.mock_docs.app import create_app as docs_app
from sources.mock_hr_api.app import create_app as hr_api_app
from sources.mock_hr_db.db import create_database
from sources.mock_idp.app import create_app as idp_app

from ekb.access.auth.options.static_tokens import StaticTokens
from ekb.app.assistant import AssistantResult
from ekb.app.choices import choose, choose_assistant, choose_query
from ekb.app.demos.mcp import Outage
from ekb.app.gateway import build_gateway
from ekb.app.lifecycle import Sources, check
from ekb.app.system import KnowledgeSystem
from ekb.contracts import Answer, AssistantAnswer, Caller, Chunk
from ekb.platform.config import load_settings
from ekb.platform.gateway.gateway import ModelGateway
from tests.http import as_httpx


def caller(user: str) -> Caller:
    return Caller(user_id=user, agent_id="hr-assistant", token_id=f"static:{user}@hr-assistant")


@dataclass
class Story:
    """The system, its sources, and everything an agent was told."""

    system: KnowledgeSystem
    sources: Sources
    outage: Outage
    gateway: ModelGateway
    profile: str
    applied: list[str] = field(default_factory=list)
    replies: list[tuple[str, str]] = field(default_factory=list)
    """``(user, reply as JSON)`` for every ``ask`` and ``search``."""

    def apply(self, action_id: str, *, sync: bool = True) -> None:
        """One scripted change in a source; then (``sync``) one round with the listings."""
        check(action_id, self.applied).apply(self.sources)
        self.applied.append(action_id)
        if sync:
            self.sync()

    def sync(self) -> None:
        self.system.sync(listings=True)

    def ask(self, user: str, question: str) -> AssistantResult:
        outcome = self.system.assistant.answer(caller(user), question)
        self.replies.append((user, outcome.reply.model_dump_json()))
        return outcome

    def search(self, user: str, question: str) -> Answer:
        answer = self.system.assistant.search(caller(user), question)
        self.replies.append((user, answer.model_dump_json()))
        return answer

    def chunks(self, key: str) -> tuple[Chunk, ...]:
        return self.system.stores.search.chunks(key)

    def links(self, key: str) -> set[str]:
        return {e for chunk in self.chunks(key) for e in chunk.entity_ids}

    def told(
        self, *needles: str, users: tuple[str, ...] | None = None, since: int = 0
    ) -> list[str]:
        """Which ``needles`` any reply (to ``users``, or to anyone; from reply number
        ``since`` on) mentioned."""
        return sorted(
            {
                needle
                for user, reply in self.replies[since:]
                if users is None or user in users
                for needle in needles
                if needle in reply
            }
        )


def pages(reply: AssistantAnswer | Answer) -> set[str]:
    if isinstance(reply, AssistantAnswer):
        return {c.document_key for c in reply.citations}
    return {p.citation.document_key for p in reply.passages}


@contextmanager
def built(
    tmp_path: Path,
    profile: str,
    *,
    writer: str = "extractive",
    docker: bool = False,
    namespace: str | None = None,
) -> Iterator[Story]:
    """The world on one profile, loaded: one sync round with the listings.

    ``writer`` is the answer option: ``extractive`` quotes and cites the passages
    (the default); ``model`` writes through the fake gateway, whose replies cite
    nothing but are cached, for the model cache's deletion check."""
    create_database(tmp_path / "hr.sqlite")
    settings = load_settings(profile=profile)
    gateway = build_gateway(load_settings(profile="ci").gateway)
    with (
        TestClient(docs_app(post=lambda url, payload: None)) as docs,
        TestClient(hr_api_app()) as hr,
        TestClient(idp_app()) as idp,
    ):
        outage = Outage(as_httpx(docs))
        with outage.client() as access_docs:
            system = KnowledgeSystem(
                as_httpx(docs),
                as_httpx(hr),
                as_httpx(idp),
                tmp_path / "hr.sqlite",
                write=choose(settings, offline=True, docker=docker, namespace=namespace),
                query=choose_query(settings),
                assistant=choose_assistant(settings).with_options("gate", answer=writer),
                gateway=gateway,
                tokens=StaticTokens(issued=()),
                access_docs=access_docs,
            )
            system.sync(listings=True)
            sources = Sources(as_httpx(docs), as_httpx(hr), as_httpx(idp))
            yield Story(system, sources, outage, gateway, profile)
