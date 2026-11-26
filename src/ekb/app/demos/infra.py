"""S17 · Real infrastructure: the same story, in memory and on Docker.

``ekb demo infra`` runs the S16 self-maintenance story (eight changes in the
sources, the agents asking after each) twice: once with the change log and the
stores in memory, once on Kafka, DynamoDB Local, SeaweedFS and OpenSearch
(``make up`` first), in a namespace of its own. Then it puts the two side by
side:

1. every promise of the story (S16's checks), and whether it held in each place;
2. what the agents were told: the pages each reply cited;
3. where the second run's data lived: the topic, the tables, the bucket, the index.

The logic is the same code in both runs; only the options of boxes 3 and 5
differ. That is the step's claim, and the demo exits 1 if a promise does not
hold in either place. Cited pages may differ in rare cases, and the demo says
why: OpenSearch's BM25 counts its statistics over the whole index (S17c).
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Annotated

import httpx
import typer

from ekb.app.choices import choose_assistant, choose_query
from ekb.app.demos._app import demo_app
from ekb.app.demos._mocks import (
    Echo,
    MockStartError,
    fail,
    mock_client,
    run_mock_command,
)
from ekb.app.demos.ingest import demo_choices
from ekb.app.demos.lifecycle import Lifecycle, run_lifecycle_demo
from ekb.app.infra import run_namespace
from ekb.contracts import AssistantAnswer
from ekb.platform.config import load_settings
from ekb.platform.gateway.errors import GatewayError

DOCS, HR, IDP, DB = (
    "sources.mock_docs",
    "sources.mock_hr_api",
    "sources.mock_idp",
    "sources.mock_hr_db",
)
PLACES = ("in memory", "on Docker")


def _cited(reply: AssistantAnswer) -> tuple[str, ...]:
    return tuple(sorted({c.document_key for c in reply.citations}))


def _story(
    docs: httpx.Client, hr: httpx.Client, idp: httpx.Client, *, docker: bool, namespace: str | None
) -> Lifecycle:
    settings = load_settings()
    write, _, _ = demo_choices(settings, False, docker=docker, namespace=namespace)
    with tempfile.TemporaryDirectory() as folder:
        db_path = Path(folder) / "hr.sqlite"
        run_mock_command(DB, ["init", "--path", str(db_path)])
        return run_lifecycle_demo(
            docs,
            hr,
            idp,
            db_path,
            echo=lambda _: None,
            write=write,
            query=choose_query(settings),
            assistant=choose_assistant(settings, offline=True),
        )


def compare(memory: Lifecycle, docker: Lifecycle, echo: Echo = typer.echo) -> bool:
    """Print the two runs side by side; return whether every promise held in both."""
    echo("1. Every promise of the story, in each place:")
    echo(f"   {'step':<18} {'in memory':<10} {'on Docker':<10} promise")
    held = True
    for left, right in zip(memory.checks, docker.checks, strict=False):
        held = held and left.held and right.held
        echo(
            f"   {left.step:<18} {'held' if left.held else 'NOT HELD':<10} "
            f"{'held' if right.held else 'NOT HELD':<10} {left.promise}"
        )
    if len(memory.checks) != len(docker.checks):
        held = False
        echo(f"   The runs checked {len(memory.checks)} and {len(docker.checks)} promises.")
    for name, story in zip(PLACES, (memory, docker), strict=True):
        echo(
            f"   {name}: leaked {story.leakage_count} times, cited a deleted page "
            f"{story.deleted_count} times"
        )
        held = held and story.leakage_count == 0 and story.deleted_count == 0

    echo("\n2. What the agents were told: the pages each reply cited.")
    pairs = list(zip(memory.replies, docker.replies, strict=False))
    same = sum(_cited(a) == _cited(b) for a, b in pairs)
    echo(f"   {same} of {len(pairs)} replies cite the same pages in both places.")
    for number, (a, b) in enumerate(pairs, start=1):
        if _cited(a) != _cited(b):
            echo(f"   reply {number} ({a.user_id}): {_cited(a)} vs {_cited(b)}")
    if same < len(pairs):
        echo("   OpenSearch counts BM25's statistics over the whole index, the in-memory")
        echo("   store over what the asker may read: the ranking can differ, never who may")
        echo("   read what (the promises above).")
    return held


def where(story: Lifecycle, echo: Echo = typer.echo) -> None:
    """Where the Docker run's data lived."""
    system = story.system
    echo("\n3. Where the Docker run's data lived:")
    for step in ("bus", "registry", "rawstore", "search", "entities", "permissions"):
        echo(f"   {system.write[step].describe()}")
    stats = system.changelog.bus.stats()
    topic = getattr(system.changelog.bus, "topic", "?")
    groups = ", ".join(f"{g.group} (lag {g.lag})" for g in stats.groups)
    partitions = len(stats.end_offsets)
    echo(f"   Kafka {topic}: {stats.events} events in {partitions} partitions; {groups}")
    records = system.changelog.registry.records()
    tombstones = sum(r.deleted for r in records)
    echo(f"   DynamoDB registry: {len(records)} items, {tombstones} tombstones")
    echo(f"   OpenSearch: {len(system.stores.search.chunks())} searchable chunks")
    members = len(system.stores.permissions.memberships())
    entities = len(system.stores.entities.entities())
    echo(f"   DynamoDB stores: {members} memberships, {entities} entities")


def run_infra_demo(
    docs: httpx.Client,
    hr: httpx.Client,
    idp: httpx.Client,
    *,
    namespace: str | None,
    echo: Echo = typer.echo,
    places: Sequence[bool] = (False, True),
) -> bool:
    """The story in memory, then on Docker (in ``namespace``); True if every promise held."""
    echo("S17 · Real infrastructure: the same story, in memory and on Docker\n")
    stories = []
    for docker in places:
        echo(f"   Running the S16 story {PLACES[docker]} …")
        stories.append(_story(docs, hr, idp, docker=docker, namespace=namespace))
    echo("")
    memory, on_docker = stories
    held = compare(memory, on_docker, echo)
    where(on_docker, echo)
    echo("\n   The same code ran both times; only boxes 3 and 5 changed options.")
    return held


@demo_app.command("infra")
def infra(
    url: Annotated[str | None, typer.Option(help="A running mock docs service.")] = None,
    hr_url: Annotated[str | None, typer.Option(help="A running mock HR API.")] = None,
    idp_url: Annotated[str | None, typer.Option(help="A running mock identity provider.")] = None,
) -> None:
    """S17 · The S16 story twice, in memory and on Kafka, DynamoDB, S3 and OpenSearch."""
    try:
        with run_namespace(True, "infra") as namespace, ExitStack() as stack:
            docs = stack.enter_context(mock_client(DOCS, url))
            hr = stack.enter_context(mock_client(HR, hr_url))
            idp = stack.enter_context(mock_client(IDP, idp_url))
            held = run_infra_demo(docs, hr, idp, namespace=namespace)
        if not held:
            raise typer.Exit(code=1)
    except (MockStartError, httpx.HTTPError, LookupError, GatewayError, ValueError, OSError) as exc:
        raise fail(exc) from exc
