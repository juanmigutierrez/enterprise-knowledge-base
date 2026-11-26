"""S12 · The worker pipeline: the full write path, T2 → T8 on the bus.

``ekb demo ingest`` connects the four mock sources (S1 to S1d) through their six
connectors (S2, S2a) to the change log (S3), and runs the **worker pipeline**
(``ekb.app.pipeline``): one consumer group, ``worker``, takes every event through
T2 … T8 with the options the active profile names (``config/``). The story:

0. the options: what the configuration names, and what runs;
1. the initial load: four sources, one line each, and who can read what;
2. one page end to end, step by step, from its trace;
3. each kind of change, followed through the line: an edit, a readers-only
   change, an unchanged save, a group change and a delete;
4. a poison event: T7's model keeps failing on one page; it is parked in the
   dead letters, the page is withdrawn, the rest moves on; the model comes
   back and a retry republishes it;
5. the totals, per status and per step.

It runs offline unless ``--models`` is given: every option that calls a model
is swapped for its offline counterpart (no header, the rules classifier, fake
vectors), and the swap is printed.
"""

from __future__ import annotations

import tempfile
import textwrap
from collections import Counter
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import httpx
import typer

from ekb.app.choices import Choices, choose
from ekb.app.connectors import build_connectors
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
from ekb.app.embed import MODEL_EMBEDDERS, embedder, probe
from ekb.app.gateway import build_gateway
from ekb.app.infra import run_namespace
from ekb.app.pipeline import (
    WORKER_GROUP,
    EventFailedError,
    Pipeline,
    build_changelog,
    build_steps,
    build_stores,
)
from ekb.connect.receiver import DOCS_WEBHOOK_PATH, create_receiver_app
from ekb.contracts import Chunk, StepOutcome, Trace, TraceStatus, step_rank
from ekb.platform.config import Settings, load_settings
from ekb.platform.gateway.errors import GatewayError
from ekb.platform.gateway.gateway import ModelGateway
from ekb.process.t7_embed.port import Embedder, Vectors
from ekb.process.t7_embed.step import EmbedStep

DOCS, HR, IDP, DB = (
    "sources.mock_docs",
    "sources.mock_hr_api",
    "sources.mock_idp",
    "sources.mock_hr_db",
)
WIDTH = 92
DANA, LUCIA = ({"X-Actor": name} for name in ("dana", "lucia"))
FOLLOWED, EDITED, REVOKED, RESAVED, DELETED = (
    "HR-PL-04",
    "HR-PL-04",
    "HR-PL-05",
    "HR-PL-01",
    "HR-PL-99",
)
POISON, NEIGHBOUR = "HR-PL-06", "HR-PL-09"
SHORT = {
    "T3 parse": "T3",
    "T4 chunk": "T4",
    "T5 classify": "T5",
    "T6 entities": "T6",
    "T7 embed": "T7",
}
SOURCES = (
    ("identity", "users", "permission store"),
    ("hr_api", "records", "entity store"),
    ("hr_db", "rows", "no store yet"),
    ("hr_docs", "pages", "search store"),
)


class ModelOutage:
    """T7's embedder, failing for the pages in ``down``: their model calls time out."""

    def __init__(self, inner: Embedder) -> None:
        self._inner = inner
        self.down: set[str] = set()

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    def embed(self, texts: Sequence[str], *, document_key: str | None = None) -> Vectors:
        if document_key in self.down:
            raise GatewayError("embed: the model timed out")
        return self._inner.embed(texts, document_key=document_key)


class Story:
    """The wiring: six connectors -> change log -> the worker pipeline -> the stores."""

    def __init__(
        self,
        docs: httpx.Client,
        hr: httpx.Client,
        idp: httpx.Client,
        db_path: Path,
        echo: Echo,
        choices: Choices,
        gateway: ModelGateway | None = None,
    ) -> None:
        self.docs, self.hr, self.idp, self.echo = docs, hr, idp, echo
        self.choices = choices
        self.changelog = build_changelog(choices)
        self.stores = build_stores(choices, self.changelog)
        steps = build_steps(choices, self.changelog, self.stores, gateway)
        self.outage = ModelOutage(
            embedder(choices.option("t7_embed"), gateway, **choices.params("t7_embed"))
        )
        self.traces: list[Trace] = []
        self.pipeline = Pipeline(
            replace(steps, t7=EmbedStep(self.outage)),
            self.changelog,
            self.stores,
            gateway=gateway,
            on_trace=self.traces.append,
        )
        self.connectors = build_connectors(docs, hr, idp, db_path, self.changelog)

    def sync(self, *, listings: bool = False) -> int:
        """The frequent connectors, then the worker drains; with ``listings``, the full
        listings next (they compare with what the bus accepted), and it drains again.
        Returns how many events the connectors put on the bus."""
        put = sum(c.sync() for c in self.connectors.frequent)
        self.pipeline.run()
        if listings:
            put += sum(c.sync() for c in self.connectors.listings)
            self.pipeline.run()
        return put

    def last(self, page: str) -> Trace:
        key = page if ":" in page else f"hr_docs:{page}"
        return [t for t in self.traces if t.document_key == key][-1]

    def users(self) -> list[str]:
        return [m.user_id for m in self.stores.permissions.memberships() if m.active]

    def finds(self, page: str) -> list[str]:
        """Who would find something of ``page`` now."""
        key = f"hr_docs:{page}"
        found = []
        for user in self.users():
            principals = self.stores.permissions.principals(user)
            if any(c.document_key == key for c in self.stores.search.readable_by(principals)):
                found.append(user)
        return found

    def lag(self) -> int:
        return {g.group: g.lag for g in self.changelog.bus.stats().groups}[WORKER_GROUP]


def _say(echo: Echo, text: str, indent: str = "   ") -> None:
    for line in textwrap.wrap(text, width=WIDTH, initial_indent=indent, subsequent_indent=indent):
        echo(line)


def _people(names: Sequence[str]) -> str:
    return ", ".join(names) or "no one"


def route(trace: Trace) -> str:
    """``T2 process -> T8 readers first -> T3 -> ... -> T8 publish = published``."""
    names = []
    for step in trace.steps:
        if step.step == "T2 diff":
            action = trace.action.value if trace.action else "?"
            names.append(f"T2 {action}")
        else:
            names.append(SHORT.get(step.step, step.step))
    return f"{' -> '.join(names)} = {trace.status.value}"


def _detail(trace: Trace, step: str) -> str:
    return next(s.detail for s in trace.steps if s.step == step)


# ------------------------------------------------------------------------ scenes


def run_ingest_demo(
    docs: httpx.Client,
    hr: httpx.Client,
    idp: httpx.Client,
    db_path: Path,
    echo: Echo = typer.echo,
    *,
    choices: Choices | None = None,
    gateway: ModelGateway | None = None,
) -> Story:
    """The whole story against the four mocks. Returns it, for the tests."""
    call(docs, "POST", "/reset")
    call(hr, "POST", "/reset")
    call(idp, "POST", "/reset")
    choices = choices or choose(load_settings(), offline=True)
    story = Story(docs, hr, idp, db_path, echo, choices, gateway)

    echo("S12 · The worker pipeline: the full write path, T2 -> T8 on the bus\n")
    _options(story)
    with serving(create_receiver_app(story.connectors.webhook.receive)) as receiver:
        hook = call(docs, "POST", "/subscriptions", json={"url": receiver.url + DOCS_WEBHOOK_PATH})
        try:
            _initial_load(story)
            _one_page(story)
            _changes(story)
            _poison(story)
        finally:
            call(docs, "DELETE", f"/subscriptions/{hook['id']}")
    _summary(story)
    return story


def _options(story: Story) -> None:
    echo, choices = story.echo, story.choices
    _say(
        echo,
        "0. One consumer group, 'worker', reads the change log. For each event, in its "
        "item's order, one handler runs T2 ... T8; T2 records the event only once every "
        "step has succeeded, so a failure anywhere means 'deliver it again'. The options "
        "come from config/:",
        indent="",
    )
    offline = "" if choices.calls_models else ", offline (--models calls them)"
    echo(f"   profile {choices.profile}{offline}:")
    for choice in choices.steps:
        settings = ", ".join(f"{k}={v}" for k, v in sorted(choice.params.items()))
        option = f"{choice.option}({settings})" if settings else choice.option
        runs = option if not choice.swapped else f"{choice.configured} -> {option} ({choice.why})"
        echo(f"     {choice.step:<15} {runs}")
    echo("")


def _initial_load(story: Story) -> None:
    echo = story.echo
    echo("1. Initial load: 6 connectors -> change log -> worker -> stores.")
    put = story.sync(listings=True)
    partitions = story.changelog.bus.partitions
    echo(f"   The connectors put {put} events on the bus ({partitions} partitions); the worker")
    echo(f"   took every one through the line ({len(story.traces)} attempts, none failed):")
    by_source = Counter(t.source for t in story.traces)
    members = story.stores.permissions.memberships()
    people = story.stores.entities.entities()
    chunks = story.stores.search.chunks()
    ready = sum(1 for c in chunks if c.publishable)
    landed = {
        "identity": f"{sum(1 for m in members if m.active)} active memberships",
        "hr_api": (
            f"{sum(1 for e in people if e.kind.value == 'person')} people, "
            f"{sum(1 for e in people if e.kind.value == 'team')} teams"
        ),
        "hr_db": "tables for SQL (metrics store, S14b)",
        "hr_docs": f"{len(chunks)} chunks, {ready} publishable",
    }
    for source, unit, store in SOURCES:
        count = f"{by_source[source]} {unit}"
        echo(f"     {source:<9} {count:<11} -> {store}: {landed[source]}")
    counts = ", ".join(
        f"{user} {len({c.document_key for c in _readable(story, user)})}" for user in story.users()
    )
    _say(echo, f"Pages each person can find: {counts}.")
    echo("")


def _readable(story: Story, user: str) -> tuple[Chunk, ...]:
    principals = story.stores.permissions.principals(user)
    return story.stores.search.readable_by(principals)


def _one_page(story: Story) -> None:
    echo, trace = story.echo, story.last(FOLLOWED)
    echo(
        f"2. One page end to end: {FOLLOWED} v{trace.version} (partition {trace.partition}, "
        f"offset {trace.offset}, attempt {trace.attempt}), from its trace:"
    )
    for step in trace.steps:
        lines = textwrap.wrap(step.detail, width=WIDTH - 34) or [""]
        echo(f"     {step.step:<17} {step.ms:7.2f} ms  {lines[0]}")
        for more in lines[1:]:
            echo(f"     {'':<17} {'':>10}  {more}")
    readers = story.finds(FOLLOWED)
    echo(f"   {trace.ms:.1f} ms in all. Searchable now for {len(readers)} people.\n")


def _changes(story: Story) -> None:
    echo = story.echo
    echo("3. Changes, each followed through the line:")

    echo(f"   dana edits {EDITED} (webhook):")
    call(
        story.docs,
        "PUT",
        f"/pages/{EDITED}",
        json={"body": "# Remote work, Milan office\n\n## The rule\nFriday from home too.\n"},
        headers=DANA,
    )
    story.sync()
    edited = story.last(EDITED)
    echo(f"     {route(edited)}")
    _say(echo, _detail(edited, "T8 publish"), indent="     ")

    echo(f"   lucia removes hr-team from {REVOKED}'s readers (webhook):")
    before = {c.chunk_id: c.vector for c in story.stores.search.chunks(f"hr_docs:{REVOKED}")}
    call(
        story.docs, "PUT", f"/pages/{REVOKED}/acl", json={"groups": ["es-employees"]}, headers=LUCIA
    )
    story.sync()
    fast = story.last(REVOKED)
    after = {c.chunk_id: c.vector for c in story.stores.search.chunks(f"hr_docs:{REVOKED}")}
    echo(f"     {route(fast)}")
    _say(
        echo,
        f"{_detail(fast, 'T8 fast lane')}. Vectors unchanged: {before == after}. "
        f"Who finds it now: {_people(story.finds(REVOKED))}.",
        indent="     ",
    )

    echo(f"   dana saves {RESAVED} without changing a word (webhook):")
    page = call(story.docs, "GET", f"/pages/{RESAVED}")
    call(story.docs, "PUT", f"/pages/{RESAVED}", json={"body": page["body"]}, headers=DANA)
    story.sync()
    skipped = story.last(RESAVED)
    echo(f"     {route(skipped)}")
    _say(echo, _detail(skipped, "T2 diff"), indent="     ")

    echo("   dana removes alice from it-employees in the identity provider (identity sync):")
    call(story.idp, "DELETE", "/scim/v2/Groups/it-employees/members/alice", headers=DANA)
    chunks = story.stores.search.chunks()
    story.sync()
    user = story.last("identity:users/alice")
    rewritten = sum(1 for o, n in zip(chunks, story.stores.search.chunks(), strict=True) if o != n)
    echo(f"     {route(user)}")
    _say(
        echo,
        f"{_detail(user, 'permission store')}; chunks rewritten: {rewritten}.",
        indent="     ",
    )

    echo(f"   dana deletes {DELETED} (webhook):")
    call(story.docs, "DELETE", f"/pages/{DELETED}", headers=DANA)
    story.sync()
    purged = story.last(DELETED)
    echo(f"     {route(purged)}")
    _say(
        echo,
        f"{_detail(purged, 'purge search')}. Who finds it now: {_people(story.finds(DELETED))}.",
        indent="     ",
    )
    echo("")


def _poison(story: Story) -> None:
    echo, key = story.echo, f"hr_docs:{POISON}"
    echo(f"4. A poison event: T7's model keeps failing on {POISON}, and only there.")
    readers = story.finds(POISON)
    story.outage.down.add(key)
    call(
        story.docs,
        "PUT",
        f"/pages/{POISON}",
        json={"body": "# Expenses\n\n## Receipts\nKeep every receipt for ten years.\n"},
        headers=DANA,
    )
    call(
        story.docs,
        "PUT",
        f"/pages/{NEIGHBOUR}",
        json={"body": "# Onboarding\n\n## Day one\nYour laptop is on your desk.\n"},
        headers=DANA,
    )
    echo(f"   dana edits {POISON}, then {NEIGHBOUR}. The worker runs three times:")
    for run in (1, 2, 3):
        try:
            if run == 1:
                story.sync()
            else:
                story.pipeline.run()
        except EventFailedError as exc:
            _say(
                echo,
                f"run {run}: {exc}. The worker stops there; nothing after it is "
                f"skipped (lag {story.lag()}).",
                indent="   ",
            )
            continue
        parked = story.last(POISON)
        _say(
            echo,
            f"run {run}: {parked.steps[-1].detail}. {NEIGHBOUR} -> "
            f"{story.last(NEIGHBOUR).status.value} (lag {story.lag()}).",
            indent="   ",
        )
    _say(
        echo,
        f"Who finds {POISON} now: {_people(story.finds(POISON))} (was {len(readers)} people). "
        "Fail closed: the old text may be out of date and its later changes wait.",
    )
    call(story.docs, "PUT", f"/pages/{POISON}/acl", json={"groups": ["hr-team"]}, headers=LUCIA)
    story.sync()
    _say(echo, f"lucia gives {POISON} to hr-team only: {story.last(POISON).steps[-1].detail}.")
    story.outage.down.clear()
    outcomes = story.pipeline.retry_parked(key)
    echo("   The model is back; the dead letters are retried in order:")
    for outcome in outcomes:
        echo(f"     v{outcome.trace.version}: {route(outcome.trace)}")
    echo(f"   Who finds {POISON} now: {_people(story.finds(POISON))}.\n")


def _summary(story: Story) -> None:
    echo = story.echo
    statuses = Counter(t.status for t in story.traces)
    line = ", ".join(f"{s.value} {statuses[s]}" for s in TraceStatus if statuses[s])
    _say(echo, f"5. The worker made {len(story.traces)} attempts: {line}.", indent="")
    echo(f"     {'step':<18}{'runs':>6}{'failed':>8}{'ms':>10}")
    totals: dict[str, list[float]] = {}
    for trace in story.traces:
        for step in trace.steps:
            runs, failed, ms = totals.get(step.step, [0, 0, 0.0])
            totals[step.step] = [
                runs + 1,
                failed + (step.outcome is StepOutcome.FAILED),
                ms + step.ms,
            ]
    for name, (runs, failed, ms) in sorted(totals.items(), key=lambda kv: step_rank(kv[0])):
        echo(f"     {name:<18}{runs:>6.0f}{failed:>8.0f}{ms:>10.1f}")
    chunks = story.stores.search.chunks()
    ready = sum(1 for c in chunks if c.publishable)
    echo(
        f"   Group {WORKER_GROUP} lag {story.lag()}; dead letters "
        f"{len(story.pipeline.dead_letters)}; search store: {len(chunks)} chunks from "
        f"{len(story.stores.search.documents())} pages, {ready} publishable."
    )


# -------------------------------------------------------------------------- CLI


def demo_choices(
    settings: Settings,
    models: bool,
    *,
    docker: bool = False,
    namespace: str | None = None,
) -> tuple[Choices, ModelGateway | None, str]:
    """The story's options and gateway; offline unless ``models``, in memory unless
    ``docker`` (S17, in ``namespace``). With models, a T7 model that cannot answer is
    swapped for the fake one, and the note says why."""
    choices = choose(settings, offline=not models, docker=docker, namespace=namespace)
    if not choices.calls_models:
        return choices, None, ""
    gateway = build_gateway(settings.gateway)
    if choices.option("t7_embed") in MODEL_EMBEDDERS:
        down = probe(gateway)
        if down is not None:
            note = f"T7's model cannot answer ({down}); running on fake vectors"
            return choices.with_options("model down", t7_embed="fake"), gateway, note
    return choices, gateway, ""


@demo_app.command("ingest")
def ingest(
    models: Annotated[
        bool,
        typer.Option(
            "--models", help="Call the models the profile names (keys in .env; Ollama for T7)."
        ),
    ] = False,
    docker: DockerFlag = False,
    url: Annotated[str | None, typer.Option(help="A running mock docs service.")] = None,
    hr_url: Annotated[str | None, typer.Option(help="A running mock HR API.")] = None,
    idp_url: Annotated[str | None, typer.Option(help="A running mock identity provider.")] = None,
) -> None:
    """S12 · The worker pipeline: the full write path, T2 -> T8 on the bus."""
    try:
        with run_namespace(docker) as namespace:
            choices, gateway, note = demo_choices(
                load_settings(), models, docker=docker, namespace=namespace
            )
            if note:
                typer.secho(f"Note: {note}.\n", fg="yellow")
            with ExitStack() as stack:
                docs = stack.enter_context(mock_client(DOCS, url))
                hr = stack.enter_context(mock_client(HR, hr_url))
                idp = stack.enter_context(mock_client(IDP, idp_url))
                db_path = Path(stack.enter_context(tempfile.TemporaryDirectory())) / "hr.sqlite"
                run_mock_command(DB, ["init", "--path", str(db_path)])
                run_ingest_demo(docs, hr, idp, db_path, choices=choices, gateway=gateway)
    except (MockStartError, httpx.HTTPError, LookupError, GatewayError, ValueError, OSError) as exc:
        raise fail(exc) from exc
