"""S16 · Self-maintenance: changes in the sources reach the answers with no manual step.

``ekb demo lifecycle`` builds the whole system (as ``ekb api`` does: mocks,
connectors, worker, stores, Q1 to Q4, the HR assistant), serves its MCP tools,
and plays the scripted story of :mod:`ekb.app.lifecycle`: eight changes made in
the sources by the people who own them. After each one it shows how the change
arrived (which connector, T2's route, what the worker did) and what the agent is
told now, compared with what was promised. Some questions are asked **before**
the worker runs, while the index is stale: revocations and deletes must already
hold then. The story:

0. the options, the MCP endpoint, the people and their test tokens;
1. a page is added; 2. edited; 3. revoked on the page; 4. revoked by removing
   lucia from a group; 5. Sara is hired in HR; 6. her identity account follows,
   and the page that named her is re-linked; 7. Lucía leaves (only the HR full
   listing sees it), and the page loses her link; 8. a page is deleted;
9. the gates: leakage, deleted content, revocations, re-links, and whether every
   step did what it promised.

Lines starting ``inspector view`` show what the agent never sees: why a cached
answer was dropped, what Q4 held back. The agent asks through MCP, with test
tokens the demo issues for the people in the story.

It runs offline (the ``extractive`` writer, no header model, fake vectors)
unless ``--models`` is given.
"""

from __future__ import annotations

import logging
import tempfile
import textwrap
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import httpx
import typer

from ekb.access.auth.options.static_tokens import IssuedToken, StaticTokens, new_token
from ekb.access.mcp.server import PATH, create_app
from ekb.app.choices import Choices, choose_assistant, choose_query
from ekb.app.demos._app import DockerFlag, demo_app
from ekb.app.demos._mocks import (
    Echo,
    MockStartError,
    call,
    fail,
    mock_client,
    run_mock_command,
)
from ekb.app.demos._serve import serving
from ekb.app.demos.ingest import demo_choices, route
from ekb.app.demos.mcp import Agent
from ekb.app.infra import run_namespace
from ekb.app.lifecycle import (
    ACTIONS,
    HOTEL_QUESTION,
    MENTORING,
    MENTORING_KEY,
    MENTORING_QUESTION,
    NEW_TRAVEL_KEY,
    OLD_TRAVEL,
    OLD_TRAVEL_KEY,
    RESTRICTED_KEY,
    Action,
    Sources,
    check,
)
from ekb.app.system import KnowledgeSystem
from ekb.connect.receiver import DOCS_WEBHOOK_PATH, create_receiver_app
from ekb.contracts import AssistantAnswer, Trace, TraceStatus
from ekb.platform.config import load_settings
from ekb.platform.gateway.errors import GatewayError
from ekb.platform.gateway.gateway import ModelGateway

DOCS, HR, IDP, DB = (
    "sources.mock_docs",
    "sources.mock_hr_api",
    "sources.mock_idp",
    "sources.mock_hr_db",
)
WIDTH = 92
AGENT = "hr-assistant"
PEOPLE = ("alice", "lucia", "bob")
"""Whose tokens the demo issues: the people who ask in the story."""


@dataclass(frozen=True)
class Check:
    """One promise of one step, and whether it held."""

    step: str
    promise: str
    held: bool


@dataclass
class Lifecycle:
    """The running story: the system, its sources, the agents, and what was checked."""

    system: KnowledgeSystem
    sources: Sources
    agents: dict[str, Agent]
    echo: Echo
    applied: list[str] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    replies: list[AssistantAnswer] = field(default_factory=list)
    marks: dict[str, int] = field(default_factory=dict)
    """For each action applied, how many replies the agent had been given before it."""
    seen: int = 0
    """How many of the system's traces were already shown."""

    @property
    def leakage_count(self) -> int:
        return sum(r.user_id != "bob" and _cites(r, RESTRICTED_KEY) for r in self.replies)

    @property
    def deleted_count(self) -> int:
        start = self.marks.get("delete_page", len(self.replies))
        return sum(_cites(r, OLD_TRAVEL_KEY) for r in self.replies[start:])

    # ---- asking

    def ask(self, user: str, question: str) -> AssistantAnswer:
        reply = self.agents[user].ask(question)
        self.replies.append(reply)
        return reply

    def show(self, user: str, question: str, page: str) -> AssistantAnswer:
        """Ask, then print in two lines: what is cited, and the line citing ``page``."""
        reply = self.ask(user, question)
        cited = [c for c in reply.citations if c.document_key == page]
        how = "a cached answer" if reply.cached else "a fresh answer"
        cited_pages = dict.fromkeys(_page(c.document_key) for c in reply.citations)
        pages = ", ".join(cited_pages) or "nothing"
        self.echo(f'     {user} asks "{question}": {how}, citing {pages}')
        if cited:
            number = cited[0].number
            line = next((ln for ln in reply.answer.splitlines() if f"[{number}]" in ln), "")
            changed = " (source changed since indexing)" if cited[0].source_changed else ""
            _say(self.echo, f"{line.strip()}{changed}", indent="       | ")
        else:
            self.echo(f"       | {_page(page)} is not cited, and nothing in the reply names it")
        self._why(user)
        return reply

    def _why(self, user: str) -> None:
        outcome = self.system.last_answer(user)
        if outcome is None:
            return
        notes: list[str] = []
        if outcome.discarded:
            notes.append(f"cached answer dropped: {outcome.discarded}")
        released = outcome.result.released if outcome.result is not None else None
        held = dict.fromkeys(
            f"{_page(w.access.document_key)} ({w.access.verdict.value})"
            for w in (released.withheld if released is not None else ())
        )
        if held:
            notes.append(f"Q4 held back {', '.join(held)}")
        if notes:
            _say(self.echo, f"inspector view: {'; '.join(notes)}.", indent="       ")

    # ---- the worker

    def worker(self, *, listings: bool, label: str = "The worker runs") -> list[Trace]:
        """One sync round (with the full listings or not), then the new traces."""
        self.system.sync(listings=listings)
        traces = list(self.system.traces)[self.seen :]
        self.seen = len(self.system.traces)
        which = "frequent connectors and full listings" if listings else "frequent connectors"
        self.echo(f"   {label} ({which}):")
        shown = [t for t in traces if t.status is not TraceStatus.SKIPPED]
        for trace in shown:
            self.echo(f"     {trace.document_key} v{trace.version}: {route(trace)}")
            for step in trace.steps:
                if step.step in _SHOWN_DETAILS:
                    _say(self.echo, f"{step.step}: {step.detail}", indent="       ")
        if not shown:
            self.echo("     nothing new reached the worker")
        return traces

    # ---- promises

    def expect(self, action: Action, promise: str, held: bool) -> None:
        self.checks.append(Check(action.id, promise, held))
        mark = "yes" if held else "NO"
        _say(self.echo, f"Promised: {promise}. Held: {mark}.")

    def links(self) -> list[str]:
        return sorted(
            {e for c in self.system.stores.search.chunks(MENTORING_KEY) for e in c.entity_ids}
        )


_SHOWN_DETAILS = frozenset(
    {"T2 diff", "entity store", "permission store", "re-link", "purge entity", "purge search"}
)


@contextmanager
def _quiet(name: str) -> Iterator[None]:
    """The person map warns about every unmapped employee, on every round; the story
    says it once, where it matters (step 5)."""
    logger = logging.getLogger(name)
    level = logger.level
    logger.setLevel(logging.ERROR)
    try:
        yield
    finally:
        logger.setLevel(level)


def _say(echo: Echo, text: str, indent: str = "   ") -> None:
    for line in textwrap.wrap(text, width=WIDTH, initial_indent=indent, subsequent_indent=indent):
        echo(line)


def _page(key: str) -> str:
    return key.partition(":")[2] or key


def _masked(token: str) -> str:
    return token[:13] + "…"


def _cites(reply: AssistantAnswer, key: str) -> bool:
    return any(c.document_key == key for c in reply.citations)


# ------------------------------------------------------------------------ scenes


def run_lifecycle_demo(
    docs: httpx.Client,
    hr: httpx.Client,
    idp: httpx.Client,
    db_path: Path,
    echo: Echo = typer.echo,
    *,
    write: Choices | None = None,
    query: Choices | None = None,
    assistant: Choices | None = None,
    gateway: ModelGateway | None = None,
) -> Lifecycle:
    """The whole story against the four mocks. Returns it, for the tests."""
    for client in (docs, hr, idp):
        call(client, "POST", "/reset")
    settings = load_settings()
    write = write or demo_choices(settings, models=False)[0]
    query = query or choose_query(settings)
    assistant = assistant or choose_assistant(settings, offline=gateway is None)
    issued = tuple(IssuedToken(user, AGENT, new_token()) for user in PEOPLE)
    system = KnowledgeSystem(
        docs,
        hr,
        idp,
        db_path,
        write=write,
        query=query,
        assistant=assistant,
        gateway=gateway,
        tokens=StaticTokens(issued=issued),
    )
    echo("S16 · Self-maintenance: changes in the sources reach the answers with no manual step\n")
    with (
        _quiet("ekb.connect.people"),
        serving(create_receiver_app(system.connectors.webhook.receive)) as receiver,
        serving(create_app(system.server("http://127.0.0.1"))) as served,
    ):
        hook = call(docs, "POST", "/subscriptions", json={"url": receiver.url + DOCS_WEBHOOK_PATH})
        try:
            url = served.url + PATH
            story = Lifecycle(
                system,
                Sources(docs, hr, idp),
                {t.user_id: Agent(url, t.token) for t in issued},
                echo,
            )
            _options(story, url, issued)
            _load(story)
            for number, action in enumerate(ACTIONS, start=1):
                _header(story, number, action)
                SCENES[action.id](story, action)
                echo("")
            _summary(story)
        finally:
            call(docs, "DELETE", f"/subscriptions/{hook['id']}")
    return story


def _options(story: Lifecycle, url: str, issued: Sequence[IssuedToken]) -> None:
    echo, system = story.echo, story.system
    _say(
        echo,
        "0. Eight changes, each made in a source by the person who owns it, through the "
        "source's own API. Nothing here touches the knowledge base: the connectors notice, "
        "the worker applies, and the agent asks through MCP. The options come from config/:",
        indent="",
    )
    echo(f"   profile {system.query.profile}:")
    for line in system.describe():
        _say(echo, line, indent="     ")
    echo(f"   MCP endpoint {url}; the docs webhook is subscribed")
    tokens = ", ".join(f"{t.user_id} {_masked(t.token)}" for t in issued)
    _say(echo, f"test tokens issued for this demo, all for agent {AGENT}: {tokens}")
    _say(
        echo,
        "alice is in all-staff; lucia is in the People team (hr-team) in Madrid; bob leads "
        "the People team and is the only one in hr-compensation.",
    )
    echo("")


def _load(story: Lifecycle) -> None:
    system = story.system
    system.sync(listings=True)
    story.seen = len(system.traces)
    chunks = system.stores.search.chunks()
    pages = len(system.stores.search.documents())
    entities = system.stores.entities.entities()
    echo = story.echo
    echo(
        f"   Loaded: {len(chunks)} searchable chunks from {pages} pages, {len(entities)} people "
        "and teams in the entity store."
    )
    echo("")


def _header(story: Lifecycle, number: int, action: Action) -> None:
    check(action.id, story.applied)
    _say(story.echo, f"{number}. {action.title}. In the {action.source}: {action.change}", "")
    _say(story.echo, f"Caught by {action.caught_by}.")


def _apply(story: Lifecycle, action: Action) -> None:
    check(action.id, story.applied).apply(story.sources)
    story.applied.append(action.id)
    story.marks[action.id] = len(story.replies)


# ---- one function per scripted step


def _add_page(story: Lifecycle, action: Action) -> None:
    _apply(story, action)
    story.worker(listings=False)
    story.echo("   After:")
    reply = story.show("alice", MENTORING_QUESTION, MENTORING_KEY)
    links = story.links()
    story.echo(f"     {MENTORING} is linked to {', '.join(links)}")
    story.expect(action, f"alice finds {MENTORING}, cited", _cites(reply, MENTORING_KEY))
    story.expect(
        action,
        "only Lucía and the People team are linked (Sara is not hired yet)",
        links == ["person:lucia", "team:T-PPL"],
    )


def _edit_page(story: Lifecycle, action: Action) -> None:
    _apply(story, action)
    story.echo("   Before the worker runs (the index still holds the old text):")
    before = story.show("alice", MENTORING_QUESTION, MENTORING_KEY)
    story.worker(listings=False)
    story.echo("   After:")
    after = story.show("alice", MENTORING_QUESTION, MENTORING_KEY)
    story.expect(
        action,
        "alice's cached answer is not reused once the page changed",
        not before.cached,
    )
    story.expect(
        action,
        "the answer says 3 times a month, and the old text is gone",
        "3 times a month" in after.answer and "2 times a month" not in after.model_dump_json(),
    )


def _revoke_page(story: Lifecycle, action: Action) -> None:
    story.ask("lucia", MENTORING_QUESTION)  # lucia's answer, kept for reuse
    before = {c.chunk_id: c.vector for c in story.system.stores.search.chunks(MENTORING_KEY)}
    _apply(story, action)
    story.echo("   Before the worker runs (the index still says all-staff):")
    alice = story.show("alice", MENTORING_QUESTION, MENTORING_KEY)
    lucia = story.show("lucia", MENTORING_QUESTION, MENTORING_KEY)
    story.worker(listings=False)
    after = {c.chunk_id: c.vector for c in story.system.stores.search.chunks(MENTORING_KEY)}
    story.echo(f"     vectors unchanged: {after == before}")
    story.expect(
        action,
        f"alice gets no trace of {MENTORING}, though her answer was cached and the index was stale",
        not alice.cached and not _cites(alice, MENTORING_KEY) and "Mentoring" not in alice.answer,
    )
    story.expect(action, f"lucia (hr-team) still gets {MENTORING}", _cites(lucia, MENTORING_KEY))
    story.expect(action, "the fast lane recomputed no vector", after == before)


def _remove_from_group(story: Lifecycle, action: Action) -> None:
    chunks = story.system.stores.search.chunks()
    _apply(story, action)
    story.echo("   Before the identity sync (the permission store still has lucia in hr-team):")
    lucia = story.show("lucia", MENTORING_QUESTION, MENTORING_KEY)
    story.worker(listings=False)
    who = story.agents["lucia"].whoami()
    story.echo(f"     whoami for lucia -> groups {', '.join(who.groups)}")
    rewritten = sum(
        1 for a, b in zip(chunks, story.system.stores.search.chunks(), strict=False) if a != b
    )
    story.echo(f"     chunks rewritten: {rewritten}")
    story.echo("   After:")
    bob = story.show("bob", MENTORING_QUESTION, MENTORING_KEY)
    story.expect(
        action,
        f"lucia no longer gets {MENTORING}, even before the sync",
        not _cites(lucia, MENTORING_KEY),
    )
    story.expect(action, "no chunk was rewritten: the page did not change", rewritten == 0)
    story.expect(action, f"bob, still in hr-team, gets {MENTORING}", _cites(bob, MENTORING_KEY))


def _hire_record(story: Lifecycle, action: Action) -> None:
    _apply(story, action)
    story.worker(listings=False)
    entity = story.system.stores.entities.get("person:sara")
    unmapped = "employee:E-1013" in story.system.connectors.people.unmapped
    _say(
        story.echo,
        "the HR delta sent E-1013 with no person: no identity user has her name, so the "
        f"connector reported it (unmapped: {unmapped}) and left it out (fail closed, D3)",
        indent="     ",
    )
    story.echo(f"     person:sara in the entity store: {entity is not None}")
    story.expect(action, "no entity for an employee with no identity account", entity is None)


def _hire_account(story: Lifecycle, action: Action) -> None:
    _apply(story, action)
    traces = story.worker(listings=True)
    links = story.links()
    story.echo(f"     {MENTORING} is linked to {', '.join(links)}")
    relinked = [t for t in traces if t.status is TraceStatus.RELINKED]
    story.expect(
        action,
        f"person:sara is an entity, and {MENTORING} was re-linked to her without an edit",
        story.system.stores.entities.get("person:sara") is not None
        and "person:sara" in links
        and [t.document_key for t in relinked] == [MENTORING_KEY],
    )


def _leaver(story: Lifecycle, action: Action) -> None:
    _apply(story, action)
    story.worker(listings=False, label="The worker runs after the HR delta")
    still = story.system.stores.entities.get("person:lucia") is not None
    story.echo(f"     person:lucia still in the entity store: {still}")
    story.worker(listings=True, label="The worker runs after the full listings")
    links = story.links()
    story.echo(f"     {MENTORING} is linked to {', '.join(links)}")
    story.expect(action, "the HR delta alone does not see the delete", still)
    story.expect(
        action,
        f"the full listing does: person:lucia is gone, and so is her link on {MENTORING}",
        story.system.stores.entities.get("person:lucia") is None and "person:lucia" not in links,
    )


def _delete_page(story: Lifecycle, action: Action) -> None:
    story.echo("   alice asks about hotels before the delete:")
    warm = story.show("alice", HOTEL_QUESTION, OLD_TRAVEL_KEY)
    forgotten = story.system.forgotten_answers
    _apply(story, action)
    story.echo("   Before the worker runs (the index still holds it):")
    before = story.show("alice", HOTEL_QUESTION, OLD_TRAVEL_KEY)
    story.worker(listings=False)
    dropped = story.system.forgotten_answers - forgotten
    story.echo(f"     cached answers that touched {OLD_TRAVEL} and were dropped: {dropped}")
    story.echo("   After:")
    after = story.show("alice", HOTEL_QUESTION, OLD_TRAVEL_KEY)
    story.expect(
        action,
        f"{OLD_TRAVEL} is held back as soon as the source deletes it",
        _cites(warm, OLD_TRAVEL_KEY) and not _cites(before, OLD_TRAVEL_KEY),
    )
    story.expect(
        action,
        f"after the purge nothing of it is in the search store or the answer cache, and "
        f"the answer cites {_page(NEW_TRAVEL_KEY)}",
        story.system.stores.search.chunks(OLD_TRAVEL_KEY) == ()
        and dropped > 0
        and not _cites(after, OLD_TRAVEL_KEY)
        and _cites(after, NEW_TRAVEL_KEY),
    )


SCENES: dict[str, Callable[[Lifecycle, Action], None]] = {
    "add_page": _add_page,
    "edit_page": _edit_page,
    "revoke_page": _revoke_page,
    "remove_from_group": _remove_from_group,
    "hire_record": _hire_record,
    "hire_account": _hire_account,
    "leaver": _leaver,
    "delete_page": _delete_page,
}


def _summary(story: Lifecycle) -> None:
    echo, system = story.echo, story.system
    echo(f"{len(ACTIONS) + 1}. The gates, over everything the agent was told in this story:")
    relinks = sum(1 for t in system.traces if t.status is TraceStatus.RELINKED)
    revoked = all(c.held for c in story.checks if c.step in {"revoke_page", "remove_from_group"})
    groups = {g.group: g.lag for g in system.changelog.bus.stats().groups}
    echo(
        f"   leakage: {_page(RESTRICTED_KEY)} cited to someone other than bob: "
        f"{story.leakage_count} times"
    )
    echo(f"   deleted content: {OLD_TRAVEL} cited after its delete: {story.deleted_count} times")
    echo(
        "   revocations effective on the next request, before the worker ran (steps 3 and "
        f"4): {'yes' if revoked else 'NO'}"
    )
    echo(f"   re-links: {relinks} pages processed again for a hire or a leaver, none edited")
    echo(
        f"   dead letters {len(system.pipeline.dead_letters)}, re-links queued "
        f"{len(system.pipeline.relinks)}, worker lag {groups.get('worker', 0)}"
    )
    held = sum(1 for c in story.checks if c.held)
    echo(f"   promises held: {held} of {len(story.checks)}")
    for failed in (c for c in story.checks if not c.held):
        _say(echo, f"NOT HELD ({failed.step}): {failed.promise}", indent="     ")


# -------------------------------------------------------------------------- CLI


@demo_app.command("lifecycle")
def lifecycle(
    models: Annotated[
        bool,
        typer.Option(
            "--models",
            help="Call the models the profile names (keys in .env; Ollama for T7).",
        ),
    ] = False,
    docker: DockerFlag = False,
    url: Annotated[str | None, typer.Option(help="A running mock docs service.")] = None,
    hr_url: Annotated[str | None, typer.Option(help="A running mock HR API.")] = None,
    idp_url: Annotated[str | None, typer.Option(help="A running mock identity provider.")] = None,
) -> None:
    """S16 · Self-maintenance: eight changes in the sources, and what the agent hears after each."""
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
                story = run_lifecycle_demo(
                    docs,
                    hr,
                    idp,
                    db_path,
                    write=write,
                    query=choose_query(settings),
                    assistant=choose_assistant(settings, offline=not models),
                    gateway=gateway,
                )
            if story.leakage_count or story.deleted_count or not all(c.held for c in story.checks):
                raise typer.Exit(code=1)
    except (MockStartError, httpx.HTTPError, LookupError, GatewayError, ValueError, OSError) as exc:
        raise fail(exc) from exc
