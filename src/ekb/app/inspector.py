"""``ekb inspector``: run every connector against the mocks and serve the inspector.

It builds a small live system in one process:

1. starts the four mock sources (or uses the ones you pass with ``--*-url``);
2. wires all six connectors (S2, S2a) to the change log (S3): events to the
   bus, originals to the raw store, the full listings against the registry;
3. loads each source once, then keeps them in sync: webhooks as they arrive,
   polling and CDC every few seconds, the full listings less often. After each
   group of connectors T2 (S4) decides every new event, purges deleted items'
   originals and records the registry, keeping its decisions for the UI. T3
   (S5) parses what T2 sends on, T4 (S6) chunks it and writes the context
   headers through the model gateway (S8), T5 (S8) labels the chunks, T6
   (S9) links them to the people and teams of the entity store, which the HR
   records keep current, T7 (S10) embeds them (bge-m3 through the gateway,
   or ``--embed fake``) and T8 (S11) publishes them to the search store with
   their readers, against the memberships the identity records keep current;
   T2's fast lane sends a readers-only change straight to T8. All of them
   forget what T2 purges, the gateway's cached replies and vectors, a deleted
   record's entity and a deleted page's published chunks included. The
   inspector's own consumer group copies the events into its log;
4. serves the read-only inspector API on 127.0.0.1 for the UI (``ui/``), with the
   model gateway's panel (S7): its routes, free quotas and the cost log that
   ``ekb demo gateway`` and the headers write to.

Change something in a mock's ``/docs`` page and watch the event appear.
"""

from __future__ import annotations

import logging
import math
import os
import sqlite3
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import InitVar, dataclass, field
from pathlib import Path
from typing import Annotated

import httpx
import typer
import uvicorn
from fastapi import FastAPI

from ekb.app.changelog import ChangeLog
from ekb.app.chunk import HEADER_OPTIONS, Processed, chunk_after_t3, chunk_step, header_writer
from ekb.app.classify import CLASSIFIER_OPTIONS, classify_step, forgetter
from ekb.app.demos._mocks import call, mock_client, run_mock_command
from ekb.app.demos._serve import serving
from ekb.app.diff import T2_GROUP, diff_step, run_t2
from ekb.app.embed import EMBEDDER_OPTIONS, MODEL_EMBEDDERS, embed_help, embed_step, probe
from ekb.app.entities import entity_store, entity_sync, link_step
from ekb.app.gateway import build_gateway, max_spend, provider_status
from ekb.app.parse import DOCUMENT_SOURCES, parse_after_t2, parse_step
from ekb.app.permissions import (
    membership_sync,
    permission_step,
    permission_store,
    readers_after_t2,
    resolver,
    search_store,
    unpublisher,
)
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
from ekb.operate.inspector.entities import EntityPanel
from ekb.operate.inspector.gateway import GatewayPanel
from ekb.operate.inspector.log import ChunkStore, DecisionLog, EventLog, ParsedStore
from ekb.operate.inspector.permissions import PermissionPanel
from ekb.platform.config import Settings, load_settings
from ekb.platform.gateway.gateway import ModelGateway
from ekb.process.t2_diff.step import DiffResult, DiffStep
from ekb.process.t3_parse.step import ParseStep
from ekb.process.t4_chunk.step import ChunkStep
from ekb.process.t5_classify.step import ClassifyStep
from ekb.process.t6_entities.step import LinkStep
from ekb.process.t7_embed.step import EmbedStep
from ekb.process.t8_permissions.step import PermissionResult, PermissionStep

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
    parsed: ParsedStore = field(default_factory=ParsedStore, kw_only=True)
    chunks: ChunkStore = field(default_factory=ChunkStore, kw_only=True)
    gateway: ModelGateway | None = field(default=None, kw_only=True)
    """The model gateway the header (and ``model_plus_rules``) call; its cached replies
    about a purged document are forgotten with the rest."""
    header: str = field(default="none", kw_only=True)
    """T4's header option: ``none`` (no model, the default for tests) or ``model``."""
    classifier: str = field(default="rules", kw_only=True)
    """T5's option: ``rules`` or ``model_plus_rules``."""
    embedder: str = field(default="fake", kw_only=True)
    """T7's option (S10): ``fake`` (no model, the default for tests) or ``model``."""
    entities: EntityPanel = field(default_factory=lambda: EntityPanel(entity_store()), kw_only=True)
    """The entity store T6 reads and the HR records fill (S9), as the inspector shows it."""
    permission_stores: InitVar[PermissionPanel | None] = field(default=None, kw_only=True)
    permissions: PermissionPanel = field(init=False)
    """The search and permission stores T8 writes and the identity records fill (S11)."""
    listing_every: int = 10
    rounds: int = 0
    errors: list[str] = field(default_factory=list)
    t2: DiffStep = field(init=False)
    t3: ParseStep = field(init=False)
    t4: ChunkStep = field(init=False)
    t5: ClassifyStep = field(init=False)
    t6: LinkStep = field(init=False)
    t7: EmbedStep = field(init=False)
    t8: PermissionStep = field(init=False)

    def __post_init__(self, permission_stores: PermissionPanel | None) -> None:
        self.permissions = permission_stores or permission_panel(self.changelog)
        published = self.permissions.lifetimes

        def readers(result: PermissionResult) -> None:
            published.record(result.event.document_key, result.event.original_lifetime)

        def processed(result: Processed) -> None:
            event, parsed = result.parse.event, result.parse.parsed
            self.parsed.record(event, parsed)
            if result.record is not None and result.record.entity is not None:
                source_key = result.record.entity.source_key
                self.entities.lifetimes.record(source_key, event.original_lifetime)
            if result.published is not None:
                readers(result.published)
            if parsed is not None and result.chunks is not None:
                headers, labels, links = result.chunks.headers, result.labels, result.links
                vectors = result.vectors
                self.chunks.record(
                    event,
                    parsed.title,
                    result.final,
                    chunker_id=result.chunks.chunker_id,
                    max_tokens=result.chunks.max_tokens,
                    header_note=headers.note if headers is not None else "",
                    classifier_note=labels.note if labels is not None else "",
                    why=labels.why if labels is not None else (),
                    linker_note=links.note if links is not None else "",
                    mentions=links.mentions if links is not None else (),
                    embed_note=vectors.reason if vectors is not None else "",
                    embedder_id=self.t7.model_id,
                )

        records = entity_sync(self.changelog, self.entities.store)
        index, stores = self.permissions.index, self.permissions.permissions
        members = membership_sync(self.changelog, stores)
        unpublish = unpublisher(index, stores, members)
        forget = forgetter(
            self.gateway, unpublish, self.parsed.drop, self.chunks.drop, records.forget
        )
        self.t3 = parse_step(self.changelog)
        self.t4 = chunk_step(header=header_writer(self.header, self.gateway))
        self.t5 = classify_step(self.classifier, self.gateway)
        self.t6 = link_step(self.entities.store)
        self.t7 = embed_step(self.embedder, self.gateway)
        self.t8 = permission_step(stores, index)
        then = chunk_after_t3(
            self.t4, processed, self.t5, self.t6, records, self.t7, self.t8, members
        )
        then_parse = readers_after_t2(
            self.t8, parse_after_t2(self.t3, then, forget), DOCUMENT_SOURCES, readers
        )

        def keep(result: DiffResult) -> None:
            then_parse(result)  # first: if T3 ... T8 fails, T2 records nothing; it comes back
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
    header: Annotated[
        str,
        typer.Option(help="T4's context header: model (through the gateway) or none."),
    ] = "model",
    classifier: Annotated[
        str, typer.Option(help="T5's classifier: rules or model_plus_rules.")
    ] = "rules",
    embed: Annotated[
        str,
        typer.Option(help="T7's embedder: model (bge-m3 through the gateway) or fake."),
    ] = "model",
) -> None:
    """8 · Inspector (local only): run the connectors and serve the read-only inspector API."""
    if not math.isfinite(interval) or interval <= 0:
        raise typer.BadParameter("must be finite and positive", param_hint="--interval")
    if header not in HEADER_OPTIONS:
        raise typer.BadParameter(f"one of {', '.join(HEADER_OPTIONS)}", param_hint="--header")
    if classifier not in CLASSIFIER_OPTIONS:
        raise typer.BadParameter(
            f"one of {', '.join(CLASSIFIER_OPTIONS)}", param_hint="--classifier"
        )
    if embed not in EMBEDDER_OPTIONS:
        raise typer.BadParameter(f"one of {', '.join(EMBEDDER_OPTIONS)}", param_hint="--embed")
    if export_openapi is not None:
        schema = openapi_json(
            _create_app(
                EventLog(),
                DecisionLog(),
                ParsedStore(),
                ChangeLog.in_memory(),
                LOCAL_PROFILE,
                chunks=ChunkStore(),
                entities=EntityPanel(entity_store()),
            )
        )
        export_openapi.write_text(schema, encoding="utf-8", newline="\n")
        typer.echo(f"wrote {export_openapi}")
        return
    settings = load_settings()
    profile = settings.profile
    log, decisions, parsed, chunks = EventLog(), DecisionLog(), ParsedStore(), ChunkStore()
    changelog = ChangeLog.in_memory()
    entities, permissions = EntityPanel(entity_store()), permission_panel(changelog)
    try:
        gateway = build_gateway(settings.gateway)
        app = _create_app(
            log,
            decisions,
            parsed,
            changelog,
            profile,
            chunks=chunks,
            gateway=gateway_panel(settings, gateway),
            entities=entities,
            permissions=permissions,
        )
    except InspectorNotAllowedError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=2) from exc
    if embed in MODEL_EMBEDDERS:
        down = probe(gateway)
        if down is not None:
            # T7 fails closed: without its model every document would fail and come back.
            help_ = embed_help("ekb inspector", "--embed fake")
            typer.secho(f"T7 cannot embed: {down}\n{help_}", fg="red", err=True)
            raise typer.Exit(code=2)

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

        loop = SyncLoop(
            connectors,
            changelog,
            log,
            decisions,
            parsed=parsed,
            chunks=chunks,
            gateway=gateway,
            header=header,
            classifier=classifier,
            embedder=embed,
            entities=entities,
            permission_stores=permissions,
            listing_every=listing_every,
        )
        loaded = loop.run_round()
        stack.enter_context(loop.running(interval))

        mapped, total = connectors.people.coverage()
        typer.echo(f"Loaded {loaded} events from 4 sources; people mapped {mapped}/{total}.")
        partitions = changelog.bus.partitions
        typer.echo(f"Change log: in-memory bus ({partitions} partitions), registry and raw store.")
        counts = ", ".join(f"{a.value} {n}" for a, n in decisions.counts().items())
        typer.echo(f"T2 decisions so far: {counts}.")
        typer.echo(f"T3 parsed {len(parsed.entries())} documents with {loop.t3.parser_id}.")
        cut = sum(len(entry.chunks) for entry in chunks.entries())
        typer.echo(f"T4 cut them into {cut} chunks with {loop.t4.chunker_id}.")
        headed = sum(1 for e in chunks.entries() for c in e.chunks if c.context_header)
        typer.echo(f"T4 headers: {headed} of {cut} chunks have one ({loop.t4.header_id}).")
        if header == "model" and cut and not headed:
            typer.echo(
                "  No model wrote them: set a free API key in .env and start with "
                "`uv run --env-file .env ekb inspector` (S7 walkthrough), or use --header none."
            )
        typer.echo(f"T5 labelled them with {loop.t5.classifier_id}.")
        known = entities.store.entities()
        linked = sum(1 for e in chunks.entries() for c in e.chunks if c.entity_ids)
        typer.echo(
            f"T6 linked {linked} of {cut} chunks to {len(known)} people and teams "
            f"({loop.t6.linker_id})."
        )
        embedded = sum(1 for e in chunks.entries() for c in e.chunks if c.vector is not None)
        typer.echo(f"T7 embedded {embedded} of {cut} chunks ({loop.t7.model_id}).")
        published = permissions.index.chunks()
        members = permissions.permissions.memberships()
        typer.echo(
            f"T8 published {len(published)} of {cut} chunks with their readers "
            f"({loop.t8.resolver_id}); {sum(1 for m in members if m.active)} active users "
            "in the permission store."
        )
        typer.echo("Make changes in the mocks (add an X-Actor header to say who):")
        for name, client in (("docs", docs), ("HR API", hr), ("identity", idp)):
            typer.echo(f"  {name:<9} {str(client.base_url).rstrip('/')}/docs")
        typer.echo(f'  database  uv run python -m sources.mock_hr_db sql --path {db_path} "..."')
        typer.echo(f"Inspector API: http://{HOST}:{port}/docs")
        typer.echo("UI: in ui/, run `npm install` once, then `npm run dev`. Ctrl+C stops.")
        uvicorn.run(app, host=HOST, port=port, log_level="warning")


def permission_panel(changelog: ChangeLog) -> PermissionPanel:
    """T8's stores as the inspector shows them: fresh in-memory search and permission
    stores, and the resolver the loop's T8 uses (S11)."""
    return PermissionPanel(
        search_store(current_lifetime=changelog.current_lifetime),
        permission_store(current_lifetime=changelog.current_lifetime),
        resolver().resolver_id,
    )


def gateway_panel(settings: Settings, gateway: ModelGateway) -> GatewayPanel:
    """The gateway as the inspector shows it: its routes, which keys are set (not the
    keys), and the cost log the demo and the pipeline write to. It reads the same log
    instance the pipeline's gateway writes, so in-memory logs (``ci``) show too."""
    config = settings.gateway
    return GatewayPanel(
        config=config,
        log=gateway.log,
        providers=provider_status(config),
        max_spend_usd=max_spend(config, os.environ),
    )


def _create_app(
    log: EventLog,
    decisions: DecisionLog,
    parsed: ParsedStore,
    changelog: ChangeLog,
    profile: str,
    *,
    chunks: ChunkStore,
    gateway: GatewayPanel | None = None,
    entities: EntityPanel | None = None,
    permissions: PermissionPanel | None = None,
) -> FastAPI:
    return create_inspector_app(
        log,
        profile=profile,
        bus=changelog.bus,
        registry=changelog.registry,
        rawstore=changelog.rawstore,
        decisions=decisions,
        parsed=parsed,
        chunks=chunks,
        current_lifetime=changelog.current_lifetime,
        gateway=gateway,
        entities=entities,
        permissions=permissions,
    )
