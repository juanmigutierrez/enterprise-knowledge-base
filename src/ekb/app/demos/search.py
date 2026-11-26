"""S13 · Q2 + Q3: how retrieval respects who is asking.

``ekb demo search`` loads the four mock sources through the worker pipeline
(S12) into the search and permission stores, then asks questions as different
people through the query path (``ekb.app.search``): Q2 turns the person into the
principals they read with now, and Q3 ranks only the chunks those admit, by
keywords and by vectors, fused. The story:

0. the options: Q2, Q3, the reranker, and how questions are embedded;
1. the load, and who is asking: each person's principals and what they can read;
2. each question, asked by each person: the keyword leg, the vector leg, the
   fused top 5, and (an inspector's view, never an agent's) what the pre-filter
   kept out;
3. a group change reaches the next search: bob leaves ``hr-compensation`` in
   the identity provider, and his next search no longer finds the salary bands,
   with no chunk rewritten;
4. people who read nothing: olivia, who has left, and a user ID no one knows.

It runs offline unless ``--models`` is given. Offline, T7's vectors are fake and
mean nothing, so questions are not embedded and Q3 runs its keyword leg alone,
and says so. With ``--models`` (bge-m3 on Ollama) both legs run.
"""

from __future__ import annotations

import tempfile
import textwrap
from collections import Counter
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Annotated

import httpx
import typer

from ekb.app.choices import Choices, choose_query
from ekb.app.connectors import build_connectors
from ekb.app.demos._app import DockerFlag, demo_app
from ekb.app.demos._mocks import Echo, MockStartError, call, fail, mock_client, run_mock_command
from ekb.app.demos.ingest import demo_choices
from ekb.app.infra import run_namespace
from ekb.app.pipeline import build_changelog, build_pipeline, build_stores
from ekb.app.search import Search, build_search, encoder_note, query_encoder
from ekb.contracts import Chunk, Hit, SearchResult
from ekb.operate.inspector.search import withheld
from ekb.platform.config import load_settings
from ekb.platform.gateway.errors import GatewayError
from ekb.platform.gateway.gateway import ModelGateway
from ekb.stores.search.port import Ranking

DOCS, HR, IDP, DB = (
    "sources.mock_docs",
    "sources.mock_hr_api",
    "sources.mock_idp",
    "sources.mock_hr_db",
)
WIDTH = 92
DANA = {"X-Actor": "dana"}
USERS = ("alice", "bob")
QUESTIONS = (
    "What are the salary bands for engineering in Italy?",
    "How many days of annual leave do I get?",
    "Ley 10/2021 acuerdo de teletrabajo",
)
SALARY = QUESTIONS[0]
REGROUPED, LEFT, UNKNOWN = "bob", "olivia", "mallory"
PAY_GROUP = "hr-compensation"
SHOWN = 3
"""Candidates shown per leg: enough to see where the fused top 5 came from."""


class World:
    """Four mocks -> six connectors -> change log -> worker -> stores <- the query path."""

    def __init__(
        self,
        docs: httpx.Client,
        hr: httpx.Client,
        idp: httpx.Client,
        db_path: Path,
        write: Choices,
        query: Choices,
        gateway: ModelGateway | None = None,
    ) -> None:
        self.idp = idp
        self.write, self.query = write, query
        self.changelog = build_changelog(write)
        self.stores = build_stores(write, self.changelog)
        self.pipeline = build_pipeline(write, self.changelog, stores=self.stores, gateway=gateway)
        self.connectors = build_connectors(docs, hr, idp, db_path, self.changelog)
        self.encoder = query_encoder(write, gateway)
        self.search: Search = build_search(query, self.stores, self.encoder)

    def sync(self, *, listings: bool = False) -> None:
        for connector in self.connectors.frequent:
            connector.sync()
        self.pipeline.run()
        if listings:
            for connector in self.connectors.listings:
                connector.sync()
            self.pipeline.run()

    def ask(self, user: str, question: str) -> SearchResult:
        return self.search.ask(user, question)


def _say(echo: Echo, text: str, indent: str = "   ") -> None:
    for line in textwrap.wrap(text, width=WIDTH, initial_indent=indent, subsequent_indent=indent):
        echo(line)


def _short(chunk: Chunk) -> str:
    """``HR-PL-01#2``: the source's ID and the chunk's position."""
    return chunk.chunk_id.partition(":")[2] or chunk.chunk_id


def _pages(chunks: Sequence[Chunk]) -> list[str]:
    return sorted({c.document_key.partition(":")[2] for c in chunks})


# ------------------------------------------------------------------------ scenes


def run_search_demo(
    docs: httpx.Client,
    hr: httpx.Client,
    idp: httpx.Client,
    db_path: Path,
    echo: Echo = typer.echo,
    *,
    users: Sequence[str] = USERS,
    questions: Sequence[str] = QUESTIONS,
    write: Choices | None = None,
    query: Choices | None = None,
    gateway: ModelGateway | None = None,
) -> World:
    """The whole story against the four mocks. Returns the world, for the tests."""
    for client in (docs, hr, idp):
        call(client, "POST", "/reset")
    settings = load_settings()
    write = write or demo_choices(settings, models=False)[0]
    query = query or choose_query(settings)
    world = World(docs, hr, idp, db_path, write, query, gateway)

    echo("S13 · Q2 + Q3: how retrieval respects who is asking\n")
    _options(world, echo)
    _load(world, echo, users)
    for number, question in enumerate(questions, start=1):
        _question(world, echo, number, question, users)
    _group_change(world, echo)
    _nobody(world, echo)
    return world


def _options(world: World, echo: Echo) -> None:
    _say(
        echo,
        "0. A question goes through Q2, which turns the person asking into the principals "
        "they read with now, then Q3, which ranks only the chunks those admit, by keywords "
        "(BM25) and by meaning (vectors), and fuses the two lists. The options come from "
        "config/:",
        indent="",
    )
    echo(f"   profile {world.query.profile}:")
    for choice in world.query.steps:
        echo(f"     {choice.describe()}")
    _say(echo, f"{encoder_note(world.write, world.encoder)}.", indent="     ")
    echo("")


def _load(world: World, echo: Echo, users: Sequence[str]) -> None:
    echo("1. The worker loads the four sources (S12), then who is asking:")
    world.sync(listings=True)
    chunks = world.stores.search.chunks()
    echo(
        f"   Search store: {len(chunks)} chunks from {len(world.stores.search.documents())} pages."
    )
    for user in users:
        scope = world.search.q2.scope(user)
        readable = world.stores.search.readable_by(scope.principals)
        hidden = len(chunks) - len(readable)
        reach = (
            f"searches {len(readable)} chunks of {len(_pages(readable))} pages; the other "
            f"{hidden} are never scored for {user}."
            if hidden
            else f"searches all {len(readable)} chunks of {len(_pages(readable))} pages."
        )
        echo(f"   {user:<7} {scope.reason}")
        echo(f"   {'':<7} {reach}")
    echo("")


def _leg(ranking: Ranking) -> str:
    """A leg's best few, as the store ranked them: ``HR-PL-01#1 4.21 · …``."""
    return " · ".join(f"{_short(s.chunk)} {s.score:.2f}" for s in ranking.hits) or "nothing"


def _ranks(hit: Hit) -> str:
    """``keyword 1 · vector 3``: where each leg that found it ranked it."""
    ranks = (("keyword", hit.keyword_rank), ("vector", hit.vector_rank))
    return " · ".join(f"{leg} {rank}" for leg, rank in ranks if rank is not None)


def _question(world: World, echo: Echo, number: int, question: str, users: Sequence[str]) -> None:
    echo(f'2.{number} "{question}"')
    index = world.stores.search
    for user in users:
        result = world.ask(user, question)
        retrieval, principals = result.retrieval, result.scope.principals
        echo(f"   asked by {user}: {retrieval.searched} chunks searched, {result.ms:.1f} ms")
        for note in retrieval.notes:
            _say(echo, f"note: {note}", indent="     ")
        if principals is not None:
            # Each leg on its own, as the store ranks it for this person (its best few).
            echo(f"     keyword  {_leg(index.keyword(question, principals, SHOWN))}")
            if retrieval.vector_leg and world.encoder is not None:
                vector = world.encoder.encode(question)
                nearest = index.nearest(vector, world.encoder.space, principals, SHOWN)
                echo(f"     vector   {_leg(nearest)}")
        echo(f"     top {len(retrieval.hits)}" if retrieval.hits else "     top 5    nothing")
        for hit in retrieval.hits:
            trail = " > ".join(hit.chunk.section) or "(no section)"
            echo(
                f"       {hit.rank}. {_short(hit.chunk):<13} {_ranks(hit):<22} rrf {hit.score:.4f}"
            )
            echo(f"          {textwrap.shorten(trail, WIDTH - 10)}")
        kept_out = withheld(index, principals, question)
        if kept_out:
            pages = Counter(c.document_key.partition(":")[2] for c in kept_out)
            listed = ", ".join(f"{page} ({n})" for page, n in sorted(pages.items()))
            _say(
                echo,
                f"inspector view: the pre-filter kept out {len(kept_out)} chunks sharing a word "
                f"with the question, from {listed}. {user} never learns they exist.",
                indent="     ",
            )
    echo("")


def _group_change(world: World, echo: Echo) -> None:
    user = REGROUPED
    echo(f"3. A group change reaches the next search: dana removes {user} from {PAY_GROUP}.")
    before = world.ask(user, SALARY).retrieval.hits
    chunks = world.stores.search.chunks()
    call(world.idp, "DELETE", f"/scim/v2/Groups/{PAY_GROUP}/members/{user}", headers=DANA)
    world.sync()
    after = world.ask(user, SALARY).retrieval.hits
    rewritten = sum(
        1 for old, new in zip(chunks, world.stores.search.chunks(), strict=True) if old != new
    )
    echo(f'   {user} asks "{SALARY}" again:')
    echo(f"     before  {', '.join(_short(h.chunk) for h in before) or 'nothing'}")
    echo(f"     after   {', '.join(_short(h.chunk) for h in after) or 'nothing'}")
    _say(
        echo,
        f"Chunks rewritten: {rewritten}. The salary bands still name {PAY_GROUP}; Q2 reads "
        f"{user}'s groups from the permission store on every question, so the identity "
        "sync's change is in the very next search.",
    )
    echo("")


def _nobody(world: World, echo: Echo) -> None:
    echo("4. People who read nothing:")
    call(world.idp, "POST", f"/scim/v2/Users/{LEFT}/deactivate", headers=DANA)
    world.sync()
    for user in (LEFT, UNKNOWN):
        result = world.ask(user, "annual leave")
        echo(f"   {user:<8} {result.scope.reason}")
        echo(f"   {'':<8} {len(result.retrieval.hits)} hits: {result.retrieval.notes[0]}.")
    _say(
        echo,
        "Q3 does not even ask the store: an unknown or inactive person has no principals, "
        "and there is nothing to filter with.",
    )


# -------------------------------------------------------------------------- CLI


@demo_app.command("search")
def search(
    question: Annotated[
        str | None, typer.Argument(help="Your own question (default: three scripted ones).")
    ] = None,
    user: Annotated[
        list[str] | None,
        typer.Option("--user", help="Who asks; repeat for several (default: alice and bob)."),
    ] = None,
    models: Annotated[
        bool,
        typer.Option("--models", help="Embed with the profile's T7 model (bge-m3 on Ollama)."),
    ] = False,
    docker: DockerFlag = False,
    url: Annotated[str | None, typer.Option(help="A running mock docs service.")] = None,
    hr_url: Annotated[str | None, typer.Option(help="A running mock HR API.")] = None,
    idp_url: Annotated[str | None, typer.Option(help="A running mock identity provider.")] = None,
) -> None:
    """S13 · Q2 + Q3: the same question, asked by different people."""
    try:
        with run_namespace(docker) as namespace:
            settings = load_settings()
            write, gateway, note = demo_choices(
                settings, models, docker=docker, namespace=namespace
            )
            if note:
                typer.secho(f"Note: {note}.\n", fg="yellow")
            with ExitStack() as stack:
                docs = stack.enter_context(mock_client(DOCS, url))
                hr = stack.enter_context(mock_client(HR, hr_url))
                idp = stack.enter_context(mock_client(IDP, idp_url))
                db_path = Path(stack.enter_context(tempfile.TemporaryDirectory())) / "hr.sqlite"
                run_mock_command(DB, ["init", "--path", str(db_path)])
                run_search_demo(
                    docs,
                    hr,
                    idp,
                    db_path,
                    users=tuple(user or USERS),
                    questions=(question,) if question else QUESTIONS,
                    write=write,
                    query=choose_query(settings),
                    gateway=gateway,
                )
    except (MockStartError, httpx.HTTPError, LookupError, GatewayError, ValueError, OSError) as exc:
        raise fail(exc) from exc
