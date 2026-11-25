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
"""

from __future__ import annotations

import math
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Annotated

import httpx
import typer
import uvicorn

from ekb.access.mcp.server import HOST, PATH, create_app
from ekb.app.choices import choose_assistant, choose_query
from ekb.app.demos._mocks import MockStartError, call, mock_client, run_mock_command
from ekb.app.demos._serve import serving
from ekb.app.demos.ingest import demo_choices
from ekb.app.system import KnowledgeSystem, connect_hint, syncing
from ekb.connect.receiver import DOCS_WEBHOOK_PATH, create_receiver_app
from ekb.platform.config import load_settings
from ekb.platform.gateway.errors import GatewayError

DEFAULT_PORT = 8080
"""The port ``compose.yaml`` gives the ``api`` service."""
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
            help="Call the models the profile names: T4, T5, T7 on the write path, and the "
            "HR assistant's answer (keys in .env; Ollama for T7).",
        ),
    ] = False,
    interval: Annotated[float, typer.Option(help="Seconds between sync rounds.")] = 3.0,
    listing_every: Annotated[
        int, typer.Option(min=1, help="Run the full listings every N rounds.")
    ] = 10,
    docs_url: Annotated[str | None, typer.Option(help="A running mock docs service.")] = None,
    hr_url: Annotated[str | None, typer.Option(help="A running mock HR API.")] = None,
    idp_url: Annotated[str | None, typer.Option(help="A running mock identity provider.")] = None,
) -> None:
    """6 · Serve the Knowledge API as MCP tools (whoami, search, ask) on 127.0.0.1."""
    if not math.isfinite(interval) or interval <= 0:
        raise typer.BadParameter("must be finite and positive", param_hint="--interval")
    try:
        settings = load_settings()
        write, gateway, note = demo_choices(settings, models)
        if note:
            typer.secho(f"Note: {note}.", fg="yellow")
        query = choose_query(settings)
        assistant = choose_assistant(settings, offline=not models)
        with ExitStack() as stack:
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
            _welcome(system, base_url + PATH, settings.profile)
            uvicorn.run(
                create_app(system.server(base_url)), host=HOST, port=port, log_level="warning"
            )
    except (MockStartError, httpx.HTTPError, LookupError, GatewayError, ValueError, OSError) as exc:
        typer.secho(f"The Knowledge API could not start: {exc}", fg="red", err=True)
        raise typer.Exit(code=1) from exc


def _welcome(system: KnowledgeSystem, url: str, profile: str) -> None:
    typer.echo(f"Knowledge API (S15), profile {profile}:")
    for line in system.describe():
        typer.echo(f"  {line}")
    chunks = system.stores.search.chunks()
    typer.echo(f"Loaded {len(chunks)} searchable chunks; kept in sync every few seconds.")
    typer.echo(f"\nMCP (Streamable HTTP): {url}   tools: whoami, search, ask")
    if system.issued:
        typer.echo("Test tokens, one per person (kept in .cache/ekb/test-tokens.json):")
        for token in system.issued:
            typer.echo(f"  {token.user_id:<7} {token.agent_id:<13} {token.token}")
        typer.echo("\nClaude Code (acting for alice):")
        typer.echo(f"  {connect_hint(url, system.issued[0].token)}")
    typer.echo("MCP Inspector: npx @modelcontextprotocol/inspector  (Streamable HTTP, the URL")
    typer.echo("  above, header Authorization: Bearer <token>). See agents/mcp_clients/.")
    typer.echo('HR assistant: uv run python -m agents.hr_assistant --token <token> "question"')
    typer.echo("Ctrl+C stops.")
