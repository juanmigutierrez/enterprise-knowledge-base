"""``ekb inspector``: run every connector against the mocks and serve the inspector.

It builds a small live system in one process:

1. starts the four mock sources (or uses the ones you pass with ``--*-url``);
2. wires all six connectors (S2, S2a) to the change log (S3): events to the
   bus, originals to the raw store, the full listings against the registry;
3. loads each source once, then keeps them in sync: webhooks as they arrive,
   polling and CDC every few seconds, the full listings less often. After each
   group of connectors T2 (S4) decides every new event, purges deleted items'
   originals and records the registry, keeping its decisions for the UI; the
   inspector's own consumer group copies the events into its log;
4. serves the read-only inspector API on 127.0.0.1 for the UI (``ui/``).

Change something in a mock's ``/docs`` page and watch the event appear.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import httpx
import typer
import uvicorn
from fastapi import FastAPI

from ekb.app.changelog import ChangeLog
from ekb.app.demos._mocks import call, mock_client, run_mock_command
from ekb.app.demos._serve import serving
from ekb.app.diff import T2_GROUP, diff_step, run_t2
from ekb.changelog.bus.consume import drain
from ekb.connect.docs_api import DocsApi
from ekb.connect.hr_api import HrApi
from ekb.connect.hr_db import CdcReader
from ekb.connect.identity_api import IdentityApi
from ekb.connect.options.docs_full_listing import DocsFullListingConnector
from ekb.connect.options.docs_webhook import DocsWebhookConnector
from ekb.connect.options.hr_api_delta import HrApiDeltaConnector
from ekb.connect.options.hr_api_full_listing import HrApiFullListingConnector
from ekb.connect.options.hr_db_cdc import HrDbCdcConnector
from ekb.connect.options.identity_sync import IdentitySyncConnector
from ekb.connect.people import PersonMap, load_people
from ekb.connect.port import Connector
from ekb.connect.receiver import DOCS_WEBHOOK_PATH, create_receiver_app
from ekb.operate.inspector.app import (
    DEFAULT_PORT,
    HOST,
    LOCAL_PROFILE,
    InspectorNotAllowedError,
    create_inspector_app,
    openapi_json,
)
from ekb.operate.inspector.log import DecisionLog, EventLog
from ekb.platform.config import load_settings
from ekb.process.t2_diff.step import DiffResult, DiffStep

logger = logging.getLogger(__name__)

INSPECTOR_GROUP = "inspector"
"""The inspector's consumer group: it reads every event, like any other consumer."""


@dataclass
class Connectors:
    """The six connectors, split by how often they run."""

    webhook: DocsWebhookConnector
    frequent: list[Connector]  # every round: webhook queue, HR delta, CDC, identity
    listings: list[Connector]  # every few rounds: the full listings that catch deletes
    people: PersonMap


def build_connectors(
    docs: httpx.Client, hr: httpx.Client, idp: httpx.Client, db_path: Path, changelog: ChangeLog
) -> Connectors:
    originals = changelog.store_original
    people = PersonMap(load_people(IdentityApi(idp), HrApi(hr)))
    sink = changelog.publish
    webhook = DocsWebhookConnector(
        DocsApi(docs), sink, originals, people, known=changelog.known("hr_docs")
    )
    return Connectors(
        webhook=webhook,
        frequent=[
            webhook,
            HrApiDeltaConnector(HrApi(hr), sink, originals, people),
            HrDbCdcConnector(CdcReader(db_path), sink, originals, people),
            IdentitySyncConnector(IdentityApi(idp), sink, originals, people),
        ],
        listings=[
            DocsFullListingConnector(
                DocsApi(docs), sink, originals, people, known=changelog.known("hr_docs")
            ),
            HrApiFullListingConnector(
                HrApi(hr), sink, originals, people, known=changelog.known("hr_api")
            ),
        ],
        people=people,
    )


_RETRYABLE = (httpx.HTTPError, sqlite3.Error, OSError, ValueError, LookupError)


@dataclass
class SyncLoop:
    """Runs the connectors in rounds. A failing connector is logged and retried next
    round; it never stops the others (a source outage must not hide other changes)."""

    connectors: Connectors
    changelog: ChangeLog
    log: EventLog
    decisions: DecisionLog = field(default_factory=DecisionLog)
    listing_every: int = 10
    rounds: int = 0
    errors: list[str] = field(default_factory=list)
    t2: DiffStep = field(init=False)

    def __post_init__(self) -> None:
        def keep(result: DiffResult) -> None:
            self.decisions.append(result.decision, result.purged)

        self.t2 = diff_step(self.changelog, downstream=keep)

    def run_round(self) -> int:
        # Frequent ones first: on round 0 the HR delta loads every record, so the HR
        # listing that follows finds nothing to add, and the docs listing loads the pages.
        # T2 records each group's events before the listings compare with the registry.
        emitted = self._sync(self.connectors.frequent)
        self._consume(T2_GROUP, self._run_t2)
        if self.rounds % self.listing_every == 0:
            emitted += self._sync(self.connectors.listings)
            self._consume(T2_GROUP, self._run_t2)
        self._consume(
            INSPECTOR_GROUP, lambda: drain(self.changelog.bus, INSPECTOR_GROUP, self.log.append)
        )
        self.rounds += 1
        return emitted

    def _run_t2(self) -> int:
        return run_t2(self.changelog, self.t2)

    def _sync(self, connectors: list[Connector]) -> int:
        emitted = 0
        for connector in connectors:
            try:
                emitted += connector.sync()
            except _RETRYABLE as exc:
                self._failed(f"{connector.source} ({type(connector).__name__}): {exc}")
        return emitted

    def _consume(self, group: str, drain_group: Callable[[], int]) -> None:
        # A failing event stays pending in its group and is retried next round.
        try:
            drain_group()
        except _RETRYABLE as exc:
            self._failed(f"consumer group {group}: {exc}")

    def _failed(self, message: str) -> None:
        self.errors.append(message)
        logger.warning("sync failed, retrying next round: %s", message)

    @contextmanager
    def running(self, interval_s: float) -> Iterator[None]:
        stop = threading.Event()

        def loop() -> None:
            while not stop.wait(interval_s):
                self.run_round()

        thread = threading.Thread(target=loop, name="connector-loop", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=interval_s + 5)


def inspector(
    port: Annotated[int, typer.Option(help="Port for the inspector API.")] = DEFAULT_PORT,
    interval: Annotated[float, typer.Option(help="Seconds between sync rounds.")] = 3.0,
    listing_every: Annotated[
        int, typer.Option(min=1, help="Run the full listings every N rounds.")
    ] = 10,
    export_openapi: Annotated[
        Path | None,
        typer.Option(help="Write the API schema (for the UI's types) to this file and exit."),
    ] = None,
    docs_url: Annotated[str | None, typer.Option(help="A running mock docs service.")] = None,
    hr_url: Annotated[str | None, typer.Option(help="A running mock HR API.")] = None,
    idp_url: Annotated[str | None, typer.Option(help="A running mock identity provider.")] = None,
) -> None:
    """8 · Inspector (local only): run the connectors and serve the read-only inspector API."""
    if not math.isfinite(interval) or interval <= 0:
        raise typer.BadParameter("must be finite and positive", param_hint="--interval")
    if export_openapi is not None:
        schema = openapi_json(
            _create_app(EventLog(), DecisionLog(), ChangeLog.in_memory(), LOCAL_PROFILE)
        )
        export_openapi.write_text(schema, encoding="utf-8", newline="\n")
        typer.echo(f"wrote {export_openapi}")
        return
    profile = load_settings().profile
    log, decisions = EventLog(), DecisionLog()
    changelog = ChangeLog.in_memory()
    try:
        app = _create_app(log, decisions, changelog, profile)
    except InspectorNotAllowedError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=2) from exc

    with ExitStack() as stack:
        docs = stack.enter_context(mock_client("sources.mock_docs", docs_url))
        hr = stack.enter_context(mock_client("sources.mock_hr_api", hr_url))
        idp = stack.enter_context(mock_client("sources.mock_idp", idp_url))
        db_path = Path(stack.enter_context(tempfile.TemporaryDirectory())) / "hr.sqlite"
        run_mock_command("sources.mock_hr_db", ["init", "--path", str(db_path)])

        connectors = build_connectors(docs, hr, idp, db_path, changelog)
        receiver = stack.enter_context(serving(create_receiver_app(connectors.webhook.receive)))
        hook = call(docs, "POST", "/subscriptions", json={"url": receiver.url + DOCS_WEBHOOK_PATH})
        stack.callback(lambda: call(docs, "DELETE", f"/subscriptions/{hook['id']}"))

        loop = SyncLoop(connectors, changelog, log, decisions, listing_every=listing_every)
        loaded = loop.run_round()
        stack.enter_context(loop.running(interval))

        mapped, total = connectors.people.coverage()
        typer.echo(f"Loaded {loaded} events from 4 sources; people mapped {mapped}/{total}.")
        partitions = changelog.bus.partitions
        typer.echo(f"Change log: in-memory bus ({partitions} partitions), registry and raw store.")
        counts = ", ".join(f"{a.value} {n}" for a, n in decisions.counts().items())
        typer.echo(f"T2 decisions so far: {counts}.")
        typer.echo("Make changes in the mocks (add an X-Actor header to say who):")
        for name, client in (("docs", docs), ("HR API", hr), ("identity", idp)):
            typer.echo(f"  {name:<9} {str(client.base_url).rstrip('/')}/docs")
        typer.echo(f'  database  uv run python -m sources.mock_hr_db sql --path {db_path} "..."')
        typer.echo(f"Inspector API: http://{HOST}:{port}/docs")
        typer.echo("UI: in ui/, run `npm install` once, then `npm run dev`. Ctrl+C stops.")
        uvicorn.run(app, host=HOST, port=port, log_level="warning")


def _create_app(
    log: EventLog, decisions: DecisionLog, changelog: ChangeLog, profile: str
) -> FastAPI:
    return create_inspector_app(
        log,
        profile=profile,
        bus=changelog.bus,
        registry=changelog.registry,
        rawstore=changelog.rawstore,
        decisions=decisions,
    )
