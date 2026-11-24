"""``ekb inspector``: run every connector against the mocks and serve the inspector.

Until the change log exists (S3) there is no bus to read, so this command
builds a small live system in one process:

1. starts the four mock sources (or uses the ones you pass with ``--*-url``);
2. wires all six connectors (S2, S2a) to an in-memory :class:`EventLog`;
3. loads each source once, then keeps them in sync: webhooks as they arrive,
   polling and CDC every few seconds, the full listings less often;
4. serves the read-only inspector API on 127.0.0.1 for the UI (``ui/``).

Change something in a mock's ``/docs`` page and watch the event appear.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import tempfile
import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import httpx
import typer
import uvicorn

from ekb.app.demos._mocks import call, mock_client, run_mock_command
from ekb.app.demos._serve import serving
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
from ekb.operate.inspector.log import EventLog
from ekb.platform.config import load_settings

logger = logging.getLogger(__name__)


class InMemoryOriginals:
    """Originals kept in memory until the raw store exists (S3)."""

    def __init__(self) -> None:
        self.saved: dict[str, bytes] = {}

    def __call__(self, document_key: str, version: str, original: bytes) -> str:
        pointer = f"mem://raw/{document_key.replace(':', '/')}/{version}"
        self.saved[pointer] = original
        return pointer


@dataclass
class Connectors:
    """The six connectors, split by how often they run."""

    webhook: DocsWebhookConnector
    frequent: list[Connector]  # every round: webhook queue, HR delta, CDC, identity
    listings: list[Connector]  # every few rounds: the full listings that catch deletes
    people: PersonMap


def build_connectors(
    docs: httpx.Client, hr: httpx.Client, idp: httpx.Client, db_path: Path, log: EventLog
) -> Connectors:
    originals = InMemoryOriginals()
    people = PersonMap(load_people(IdentityApi(idp), HrApi(hr)))
    sink = log.append
    webhook = DocsWebhookConnector(
        DocsApi(docs), sink, originals, people, known=lambda: log.known("hr_docs")
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
                DocsApi(docs), sink, originals, people, known=lambda: log.known("hr_docs")
            ),
            HrApiFullListingConnector(
                HrApi(hr), sink, originals, people, known=lambda: log.known("hr_api")
            ),
        ],
        people=people,
    )


@dataclass
class SyncLoop:
    """Runs the connectors in rounds. A failing connector is logged and retried next
    round; it never stops the others (a source outage must not hide other changes)."""

    connectors: Connectors
    listing_every: int = 10
    rounds: int = 0
    errors: list[str] = field(default_factory=list)

    def run_round(self) -> int:
        # Frequent ones first: on round 0 the HR delta loads every record, so the HR
        # listing that follows finds nothing to add, and the docs listing loads the pages.
        due = self.rounds % self.listing_every == 0
        group = self.connectors.frequent + (self.connectors.listings if due else [])
        emitted = 0
        for connector in group:
            try:
                emitted += connector.sync()
            except (httpx.HTTPError, sqlite3.Error, OSError, ValueError, LookupError) as exc:
                message = f"{connector.source} ({type(connector).__name__}): {exc}"
                self.errors.append(message)
                logger.warning("sync failed, retrying next round: %s", message)
        self.rounds += 1
        return emitted

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
        schema = openapi_json(create_inspector_app(EventLog(), profile=LOCAL_PROFILE))
        export_openapi.write_text(schema, encoding="utf-8", newline="\n")
        typer.echo(f"wrote {export_openapi}")
        return
    profile = load_settings().profile
    log = EventLog()
    try:
        app = create_inspector_app(log, profile=profile)
    except InspectorNotAllowedError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=2) from exc

    with ExitStack() as stack:
        docs = stack.enter_context(mock_client("sources.mock_docs", docs_url))
        hr = stack.enter_context(mock_client("sources.mock_hr_api", hr_url))
        idp = stack.enter_context(mock_client("sources.mock_idp", idp_url))
        db_path = Path(stack.enter_context(tempfile.TemporaryDirectory())) / "hr.sqlite"
        run_mock_command("sources.mock_hr_db", ["init", "--path", str(db_path)])

        connectors = build_connectors(docs, hr, idp, db_path, log)
        receiver = stack.enter_context(serving(create_receiver_app(connectors.webhook.receive)))
        hook = call(docs, "POST", "/subscriptions", json={"url": receiver.url + DOCS_WEBHOOK_PATH})
        stack.callback(lambda: call(docs, "DELETE", f"/subscriptions/{hook['id']}"))

        loop = SyncLoop(connectors, listing_every=listing_every)
        loaded = loop.run_round()
        stack.enter_context(loop.running(interval))

        mapped, total = connectors.people.coverage()
        typer.echo(f"Loaded {loaded} events from 4 sources; people mapped {mapped}/{total}.")
        typer.echo("Make changes in the mocks (add an X-Actor header to say who):")
        for name, client in (("docs", docs), ("HR API", hr), ("identity", idp)):
            typer.echo(f"  {name:<9} {str(client.base_url).rstrip('/')}/docs")
        typer.echo(f'  database  uv run python -m sources.mock_hr_db sql --path {db_path} "..."')
        typer.echo(f"Inspector API: http://{HOST}:{port}/docs")
        typer.echo("UI: in ui/, run `npm install` once, then `npm run dev`. Ctrl+C stops.")
        uvicorn.run(app, host=HOST, port=port, log_level="warning")
