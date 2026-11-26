"""``ekb api``: the Knowledge API, serving the HR assistant to agents over MCP (S15).

It builds the whole POC in one process (:class:`~ekb.app.system.KnowledgeSystem`:
connectors, change log, worker, stores, the query path and the HR assistant), as
``ekb inspector`` does until the change log is durable (S17). ``ekb api``
starts the mocks (or uses the ones you pass), loads them once, keeps them in
sync (webhooks as they arrive, polling every few seconds, the full listings
less often) and serves MCP over Streamable HTTP on ``127.0.0.1`` only. It
prints the test tokens (``auth: static_tokens``) and the command that connects
Claude Code. It runs offline (fake vectors, the ``extractive`` writer) unless
``--models`` is given.

``ekb demo mcp`` builds the same system and tells its story.

**On Docker (S17).** ``--docker`` keeps the one process but runs the change log
and the stores on the real infrastructure, in a namespace of its own.
``--query-only`` is the third service of ``docker compose --profile app up``:
no connectors and no worker (``ekb connect`` and ``ekb worker`` run apart), just
the query path over the shared stores, and the ``answer-cache`` consumer group
that empties cached answers when a page's delete reaches the bus
(:mod:`ekb.app.services`).
"""

from __future__ import annotations

import math
import tempfile
import threading
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Annotated

import httpx
import typer
import uvicorn

from ekb.access.auth.options.static_tokens import IssuedToken
from ekb.access.mcp.server import HOST, PATH, create_app, create_server
from ekb.app.choices import choose_assistant, choose_query
from ekb.app.demos._mocks import MockStartError, call, mock_client, run_mock_command
from ekb.app.demos._serve import serving
from ekb.app.demos.ingest import demo_choices
from ekb.app.infra import START_HINT, SharedInfrastructureError, run_namespace
from ekb.app.services import QueryService, run_rounds
from ekb.app.system import KnowledgeSystem, connect_hint, syncing
from ekb.connect.receiver import DOCS_WEBHOOK_PATH, create_receiver_app
from ekb.platform.config import load_settings
from ekb.platform.gateway.errors import GatewayError
from ekb.platform.infra import InfrastructureError
from ekb.stores.search.port import SearchStore

DEFAULT_PORT = 8080
"""The port ``compose.yaml`` gives the ``api`` service."""
QUERY_DOCS_URL, QUERY_IDP_URL = "http://127.0.0.1:8081", "http://127.0.0.1:8083"
"""Where ``--query-only`` checks access now (Q4) when no URL is given: the mocks' ports."""
DOCS, HR, IDP, DB = (
    "sources.mock_docs",
    "sources.mock_hr_api",
    "sources.mock_idp",
    "sources.mock_hr_db",
)


def api(
    port: Annotated[int, typer.Option(help="Port for the Knowledge API.")] = DEFAULT_PORT,
    models: Annotated[
        bool,
        typer.Option(
            "--models",
            envvar="EKB_MODELS",
            help="Call the models the profile names: T4, T5, T7 on the write path, and the "
            "HR assistant's answer (keys in .env; Ollama for T7).",
        ),
    ] = False,
    interval: Annotated[float, typer.Option(help="Seconds between sync rounds.")] = 3.0,
    listing_every: Annotated[
        int, typer.Option(min=1, help="Run the full listings every N rounds.")
    ] = 10,
    docs_url: Annotated[
        str | None, typer.Option(envvar="EKB_DOCS_URL", help="A running mock docs service.")
    ] = None,
    hr_url: Annotated[
        str | None, typer.Option(envvar="EKB_HR_URL", help="A running mock HR API.")
    ] = None,
    idp_url: Annotated[
        str | None, typer.Option(envvar="EKB_IDP_URL", help="A running mock identity provider.")
    ] = None,
    docker: Annotated[
        bool,
        typer.Option(
            "--docker",
            help="Run the change log and the stores on the real infrastructure (make up "
            "first), in a namespace of this run's own, dropped when it stops (S17).",
        ),
    ] = False,
    query_only: Annotated[
        bool,
        typer.Option(
            "--query-only",
            envvar="EKB_QUERY_ONLY",
            help="Serve the query path only, on the real infrastructure: the connectors "
            "and the worker run as ekb connect and ekb worker (docker compose --profile "
            "app up, S17).",
        ),
    ] = False,
    bind: Annotated[
        str,
        typer.Option(envvar="EKB_BIND", help="Where to listen (0.0.0.0 inside a container only)."),
    ] = HOST,
) -> None:
    """6 · Serve the Knowledge API as MCP tools (whoami, search, ask) on 127.0.0.1."""
    if not math.isfinite(interval) or interval <= 0:
        raise typer.BadParameter("must be finite and positive", param_hint="--interval")
    if query_only:
        _serve_query_only(port, bind, models, interval, docs_url, idp_url)
        return
    try:
        settings = load_settings()
        with ExitStack() as stack:
            namespace = stack.enter_context(run_namespace(docker, "api"))
            write, gateway, note = demo_choices(
                settings, models, docker=docker, namespace=namespace
            )
            if note:
                typer.secho(f"Note: {note}.", fg="yellow")
            query = choose_query(settings)
            assistant = choose_assistant(settings, offline=not models)
            docs = stack.enter_context(mock_client(DOCS, docs_url))
            hr = stack.enter_context(mock_client(HR, hr_url))
            idp = stack.enter_context(mock_client(IDP, idp_url))
            db_path = Path(stack.enter_context(tempfile.TemporaryDirectory())) / "hr.sqlite"
            run_mock_command(DB, ["init", "--path", str(db_path)])
            system = KnowledgeSystem(
                docs,
                hr,
                idp,
                db_path,
                write=write,
                query=query,
                assistant=assistant,
                gateway=gateway,
            )
            receiver = stack.enter_context(
                serving(create_receiver_app(system.connectors.webhook.receive))
            )
            hook = call(
                docs, "POST", "/subscriptions", json={"url": receiver.url + DOCS_WEBHOOK_PATH}
            )
            stack.callback(lambda: call(docs, "DELETE", f"/subscriptions/{hook['id']}"))
            system.sync(listings=True)
            stack.enter_context(syncing(system, interval, listing_every))
            base_url = f"http://{HOST}:{port}"
            _welcome(
                system.describe(),
                system.stores.search,
                system.issued,
                base_url + PATH,
                settings.profile,
            )
            uvicorn.run(
                create_app(system.server(base_url)), host=bind, port=port, log_level="warning"
            )
    except (
        MockStartError,
        httpx.HTTPError,
        LookupError,
        GatewayError,
        ValueError,
        OSError,
    ) as exc:
        hint = f" ({START_HINT})" if isinstance(exc, InfrastructureError) else ""
        typer.secho(f"The Knowledge API could not start: {exc}{hint}", fg="red", err=True)
        raise typer.Exit(code=1) from exc


def _serve_query_only(
    port: int,
    bind: str,
    models: bool,
    interval: float,
    docs_url: str | None,
    idp_url: str | None,
) -> None:
    """The Knowledge API as its own service (S17): Q1 … Q4 and the HR assistant over the
    shared stores, and the answer-cache group reading the deletes off the bus."""
    stop = threading.Event()
    try:
        settings = load_settings()
        write, gateway, note = demo_choices(settings, models, docker=True)
        if note:
            typer.secho(f"Note: {note}.", fg="yellow")
        with (
            httpx.Client(base_url=docs_url or QUERY_DOCS_URL, timeout=10.0) as docs,
            httpx.Client(base_url=idp_url or QUERY_IDP_URL, timeout=10.0) as idp,
        ):
            service = QueryService(
                docs,
                idp,
                write=write,
                query=choose_query(settings),
                assistant=choose_assistant(settings, offline=not models),
                gateway=gateway,
            )
            forgetting = threading.Thread(
                target=run_rounds,
                args=(service.round,),
                kwargs={
                    "interval_s": interval,
                    "listing_every": 1,
                    "stop": stop,
                    "name": "answer-cache",
                },
                name="answer-cache",
                daemon=True,
            )
            forgetting.start()
            base_url = f"http://{HOST}:{port}"
            issued = tuple(
                t for t in getattr(service.tokens, "issued", ()) if isinstance(t, IssuedToken)
            )
            lines = [c.describe() for c in write.steps if c.step in ("search", "permissions")]
            _welcome(lines, service.stores.search, issued, base_url + PATH, settings.profile)
            uvicorn.run(
                create_app(create_server(service.assistant, service.tokens, base_url=base_url)),
                host=bind,
                port=port,
                log_level="warning",
            )
    except SharedInfrastructureError as exc:
        typer.secho(f"The Knowledge API cannot start: {exc}", fg="red", err=True)
        raise typer.Exit(code=2) from exc
    except (httpx.HTTPError, LookupError, GatewayError, ValueError, OSError) as exc:
        hint = f" ({START_HINT})" if isinstance(exc, InfrastructureError) else ""
        typer.secho(f"The Knowledge API could not start: {exc}{hint}", fg="red", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        stop.set()


def _welcome(
    lines: Sequence[str],
    search: SearchStore,
    issued: Sequence[IssuedToken],
    url: str,
    profile: str,
) -> None:
    typer.echo(f"Knowledge API (S15), profile {profile}:")
    for line in lines:
        typer.echo(f"  {line}")
    chunks = search.chunks()
    typer.echo(f"Loaded {len(chunks)} searchable chunks; kept in sync every few seconds.")
    typer.echo(f"\nMCP (Streamable HTTP): {url}   tools: whoami, search, ask")
    if issued:
        typer.echo("Test tokens, one per person (kept in .cache/ekb/test-tokens.json):")
        for token in issued:
            typer.echo(f"  {token.user_id:<7} {token.agent_id:<13} {token.token}")
        typer.echo("\nClaude Code (acting for alice):")
        typer.echo(f"  {connect_hint(url, issued[0].token)}")
    typer.echo("MCP Inspector: npx @modelcontextprotocol/inspector  (Streamable HTTP, the URL")
    typer.echo("  above, header Authorization: Bearer <token>). See agents/mcp_clients/.")
    typer.echo('HR assistant: uv run python -m agents.hr_assistant --token <token> "question"')
    typer.echo("Ctrl+C stops.")
