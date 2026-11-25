"""``ekb inspector``: run every connector against the mocks and serve the inspector.

It builds a small live system in one process:

1. starts the four mock sources (or uses the ones you pass with ``--*-url``);
2. wires all six connectors (S2, S2a) to the change log (S3): events to the
   bus, originals to the raw store, the full listings against the registry;
3. loads each source once, then keeps them in sync: webhooks as they arrive,
   polling and CDC every few seconds, the full listings less often. After each
   group of connectors the **worker pipeline** (S12, ``ekb.app.pipeline``,
   consumer group ``worker``) runs every new event through T2 … T8 with the
   options the active profile names (``--header``, ``--classifier`` and
   ``--embed`` override them): T2 decides, T3 parses, T4 chunks and writes the
   context headers, T5 labels, T6 links, T7 embeds and T8 publishes to the
   search store with the readers; records fill the entity and permission
   stores; a readers-only change takes the fast lane; a purge reaches every
   store. The inspector keeps what each step produced for its panels, and every
   attempt's trace for the **Pipeline** tab. An event that keeps failing is
   parked after three attempts (the dead letters) instead of blocking the rest.
   The inspector's own consumer group copies the events into its log;
4. serves the read-only inspector API on 127.0.0.1 for the UI (``ui/``), with the
   model gateway's panel (S7): its routes, free quotas and the cost log that
   ``ekb demo gateway`` and the headers write to; and the query path (S13,
   ``ekb.app.search``) over the same stores, for the **Search as user** tab: Q2
   and Q3 as the profile names them, questions embedded by T7's model.

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
from ekb.app.choices import Choices, choose, choose_query
from ekb.app.chunk import HEADER_OPTIONS
from ekb.app.classify import CLASSIFIER_OPTIONS
from ekb.app.connectors import Connectors, build_connectors
from ekb.app.demos._mocks import call, mock_client, run_mock_command
from ekb.app.demos._serve import serving
from ekb.app.embed import EMBEDDER_OPTIONS, MODEL_EMBEDDERS, embed_help, probe
from ekb.app.entities import entity_store
from ekb.app.gateway import build_gateway, max_spend, provider_status
from ekb.app.permissions import permission_store, resolver, search_store
from ekb.app.pipeline import (
    WORKER_GROUP,
    EventFailedError,
    Outcome,
    Pipeline,
    Stores,
    build_changelog,
    build_pipeline,
    build_stores,
)
from ekb.app.search import build_search, encoder_note, query_encoder
from ekb.changelog.bus.consume import drain
from ekb.connect.port import Connector
from ekb.connect.receiver import DOCS_WEBHOOK_PATH, create_receiver_app
from ekb.contracts import DiffAction
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
from ekb.operate.inspector.search import SearchPanel
from ekb.operate.inspector.trace import TraceLog
from ekb.platform.config import Settings, load_settings
from ekb.platform.gateway.gateway import ModelGateway
from ekb.process.t2_diff.step import DiffStep
from ekb.process.t3_parse.step import ParseStep
from ekb.process.t4_chunk.step import ChunkStep
from ekb.process.t5_classify.step import ClassifyStep
from ekb.process.t6_entities.step import LinkStep
from ekb.process.t7_embed.step import EmbedStep
from ekb.process.t8_permissions.step import PermissionStep

logger = logging.getLogger(__name__)

INSPECTOR_GROUP = "inspector"
"""The inspector's consumer group: it reads every event, like any other consumer."""


_RETRYABLE = (httpx.HTTPError, sqlite3.Error, OSError, ValueError, LookupError, EventFailedError)
"""What a round logs and retries next round. A worker's failed attempt is one of them: the
event stays pending and is delivered again (and parked after three, S12)."""


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
    """The model gateway the model options call; a purged document's cached replies and
    vectors are forgotten through it."""
    choices: Choices | None = field(default=None, kw_only=True)
    """The options the worker runs (S12). ``None``: the local profile, offline (no
    header, the rules classifier, fake vectors), which is what the tests use."""
    header: str | None = field(default=None, kw_only=True)
    """T4's header option, over ``choices``: ``none`` or ``model`` (``--header``)."""
    classifier: str | None = field(default=None, kw_only=True)
    """T5's option, over ``choices``: ``rules`` or ``model_plus_rules`` (``--classifier``)."""
    embedder: str | None = field(default=None, kw_only=True)
    """T7's option, over ``choices``: ``fake`` or ``model`` (``--embed``)."""
    entities: EntityPanel = field(default_factory=lambda: EntityPanel(entity_store()), kw_only=True)
    """The entity store T6 reads and the HR records fill (S9), as the inspector shows it."""
    permission_stores: InitVar[PermissionPanel | None] = field(default=None, kw_only=True)
    permissions: PermissionPanel = field(init=False)
    """The search and permission stores T8 writes and the identity records fill (S11)."""
    traces: TraceLog = field(default_factory=TraceLog, kw_only=True)
    """Every attempt the worker made, step by step (S12)."""
    listing_every: int = 10
    rounds: int = 0
    errors: list[str] = field(default_factory=list)
    pipeline: Pipeline = field(init=False)
    t2: DiffStep = field(init=False)
    t3: ParseStep = field(init=False)
    t4: ChunkStep = field(init=False)
    t5: ClassifyStep = field(init=False)
    t6: LinkStep = field(init=False)
    t7: EmbedStep = field(init=False)
    t8: PermissionStep = field(init=False)

    def __post_init__(self, permission_stores: PermissionPanel | None) -> None:
        self.permissions = permission_stores or permission_panel(self.changelog)
        choices = self.choices or choose(load_settings(profile=LOCAL_PROFILE), offline=True)
        self.choices = choices.with_options(
            "command line",
            t4_header=self.header,
            t5_classify=self.classifier,
            t7_embed=self.embedder,
        )
        stores = Stores(
            search=self.permissions.index,
            permissions=self.permissions.permissions,
            entities=self.entities.store,
        )
        self.pipeline = build_pipeline(
            self.choices,
            self.changelog,
            stores=stores,
            gateway=self.gateway,
            observers=[self._keep],
            on_trace=self.traces.append,
        )
        steps = self.pipeline.steps
        self.t2, self.t3, self.t4, self.t5 = self.pipeline.t2, steps.t3, steps.t4, steps.t5
        self.t6, self.t7, self.t8 = steps.t6, steps.t7, steps.t8

    def _keep(self, outcome: Outcome) -> None:
        """What each step produced, for the panels. Runs before T2 records the event:
        if it fails, the event comes back (S4)."""
        lifetimes = self.permissions.lifetimes
        if outcome.readers is not None:
            event = outcome.readers.event
            lifetimes.record(event.document_key, event.original_lifetime)
        result = outcome.processed
        if result is not None:
            event, parsed = result.parse.event, result.parse.parsed
            self.parsed.record(event, parsed)
            if result.record is not None and result.record.entity is not None:
                source_key = result.record.entity.source_key
                self.entities.lifetimes.record(source_key, event.original_lifetime)
            if result.published is not None:
                lifetimes.record(event.document_key, event.original_lifetime)
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
        if outcome.diff is not None:
            decision = outcome.diff.decision
            if decision.action is DiffAction.PURGE:  # deleted content is never shown
                self.parsed.drop(decision.event.document_key)
                self.chunks.drop(decision.event.document_key)
            self.decisions.append(decision, outcome.diff.purged)

    def run_round(self) -> int:
        # Frequent ones first: on round 0 the HR delta loads every record, so the HR
        # listing that follows finds nothing to add, and the docs listing loads the pages.
        # Listings compare with versions already accepted from the frequent connectors.
        emitted = self._sync(self.connectors.frequent)
        self._consume(WORKER_GROUP, self.pipeline.run)
        if self.rounds % self.listing_every == 0:
            emitted += self._sync(self.connectors.listings)
            self._consume(WORKER_GROUP, self.pipeline.run)
        self._consume(
            INSPECTOR_GROUP, lambda: drain(self.changelog.bus, INSPECTOR_GROUP, self.log.append)
        )
        self.rounds += 1
        return emitted

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
        str | None,
        typer.Option(
            help="T4's context header: model (through the gateway) or none. "
            "Default: the profile's t4_header."
        ),
    ] = None,
    classifier: Annotated[
        str | None,
        typer.Option(
            help="T5's classifier: rules or model_plus_rules. Default: the profile's t5_classify."
        ),
    ] = None,
    embed: Annotated[
        str | None,
        typer.Option(
            help="T7's embedder: model (bge-m3 through the gateway) or fake. "
            "Default: the profile's t7_embed."
        ),
    ] = None,
) -> None:
    """8 · Inspector (local only): run the connectors and serve the read-only inspector API."""
    if not math.isfinite(interval) or interval <= 0:
        raise typer.BadParameter("must be finite and positive", param_hint="--interval")
    if header is not None and header not in HEADER_OPTIONS:
        raise typer.BadParameter(f"one of {', '.join(HEADER_OPTIONS)}", param_hint="--header")
    if classifier is not None and classifier not in CLASSIFIER_OPTIONS:
        raise typer.BadParameter(
            f"one of {', '.join(CLASSIFIER_OPTIONS)}", param_hint="--classifier"
        )
    if embed is not None and embed not in EMBEDDER_OPTIONS:
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
    choices = choose(settings).with_options(
        "command line", t4_header=header, t5_classify=classifier, t7_embed=embed
    )
    log, decisions, parsed, chunks = EventLog(), DecisionLog(), ParsedStore(), ChunkStore()
    traces = TraceLog()
    changelog = build_changelog(choices)
    stores = build_stores(choices, changelog)
    entities = EntityPanel(stores.entities)
    permissions = PermissionPanel(
        stores.search, stores.permissions, resolver(choices.option("t8_permissions")).resolver_id
    )
    try:
        gateway = build_gateway(settings.gateway)
        query = choose_query(settings)
        encoder = query_encoder(choices, gateway)
        search = SearchPanel(
            ask=build_search(query, stores, encoder).ask,
            index=stores.search,
            encoder=encoder_note(choices, encoder),
        )
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
            traces=traces,
            search=search,
        )
    except InspectorNotAllowedError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=2) from exc
    if choices.option("t7_embed") in MODEL_EMBEDDERS:
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
            choices=choices,
            entities=entities,
            permission_stores=permissions,
            traces=traces,
            listing_every=listing_every,
        )
        typer.echo(f"Options (profile {profile}; config/ names them):")
        for choice in choices.steps:
            typer.echo(f"  {choice.describe()}")
        loaded = loop.run_round()
        stack.enter_context(loop.running(interval))

        mapped, total = connectors.people.coverage()
        typer.echo(f"Loaded {loaded} events from 4 sources; people mapped {mapped}/{total}.")
        partitions = changelog.bus.partitions
        typer.echo(f"Change log: {partitions}-partition bus, registry and raw store.")
        counts = ", ".join(f"{a.value} {n}" for a, n in decisions.counts().items())
        typer.echo(f"Worker (group {WORKER_GROUP}) T2 decisions so far: {counts}.")
        typer.echo(f"T3 parsed {len(parsed.entries())} documents with {loop.t3.parser_id}.")
        cut = sum(len(entry.chunks) for entry in chunks.entries())
        typer.echo(f"T4 cut them into {cut} chunks with {loop.t4.chunker_id}.")
        headed = sum(1 for e in chunks.entries() for c in e.chunks if c.context_header)
        typer.echo(f"T4 headers: {headed} of {cut} chunks have one ({loop.t4.header_id}).")
        if choices.option("t4_header") == "model" and cut and not headed:
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
        typer.echo(
            "Q2 + Q3: " + "; ".join(c.describe() for c in query.steps) + f"; {search.encoder}."
        )
        typer.echo("  Ask as anyone, two people side by side, in the Search as user tab.")
        parked = len(loop.pipeline.dead_letters)
        typer.echo(
            f"Dead letters: {parked} parked events. Follow any document in the Pipeline tab."
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
    traces: TraceLog | None = None,
    search: SearchPanel | None = None,
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
        traces=traces,
        search=search,
    )
