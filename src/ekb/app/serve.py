"""``ekb connect`` and ``ekb worker``: two of the three services (S17).

``docker compose --profile app up`` runs them as containers of the one image,
next to ``ekb api --query-only`` (:mod:`ekb.app.api`), the mock sources and the
infrastructure. They also run from a terminal on the host, against
``make up``'s infrastructure and mocks started by hand, with the same flags.

Where the sources are comes from flags, or the environment variables Compose
sets (``EKB_DOCS_URL`` …); where the infrastructure is, from ``EKB_KAFKA_BOOTSTRAP``
and the others (:mod:`ekb.platform.infra`). Both run until stopped (Ctrl+C, or
``docker compose stop``, which sends SIGTERM), finishing the round they are in.
"""

from __future__ import annotations

import logging
import threading
from contextlib import ExitStack
from pathlib import Path
from typing import Annotated

import httpx
import typer

from ekb.app.choices import choose
from ekb.app.demos._mocks import call
from ekb.app.demos._serve import serving
from ekb.app.demos.ingest import demo_choices
from ekb.app.infra import START_HINT, SharedInfrastructureError
from ekb.app.services import ConnectService, WorkerService, run_rounds, stop_on_signals
from ekb.connect.receiver import DOCS_WEBHOOK_PATH, create_receiver_app
from ekb.platform.config import load_settings
from ekb.platform.gateway.errors import GatewayError
from ekb.platform.infra import InfrastructureError

logger = logging.getLogger(__name__)

DOCS_URL = "http://127.0.0.1:8081"
HR_URL = "http://127.0.0.1:8082"
IDP_URL = "http://127.0.0.1:8083"
RECEIVER_PORT = 8090

Interval = Annotated[float, typer.Option(min=0.1, help="Seconds between rounds.")]
ListingEvery = Annotated[
    int, typer.Option(min=1, help="Run the full listings every N rounds (the first one too).")
]


def _logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def connect(
    docs_url: Annotated[str, typer.Option(envvar="EKB_DOCS_URL", help="The docs tool.")] = DOCS_URL,
    hr_url: Annotated[str, typer.Option(envvar="EKB_HR_URL", help="The HR record API.")] = HR_URL,
    idp_url: Annotated[
        str, typer.Option(envvar="EKB_IDP_URL", help="The identity provider.")
    ] = IDP_URL,
    hr_db: Annotated[
        Path, typer.Option(envvar="EKB_HR_DB", help="The HR database file (SQLite, S1d).")
    ] = Path("data/hr.sqlite"),
    bind: Annotated[
        str, typer.Option(envvar="EKB_BIND", help="Where the webhook receiver listens.")
    ] = "127.0.0.1",
    receiver_port: Annotated[
        int, typer.Option(envvar="EKB_RECEIVER_PORT", help="The webhook receiver's port.")
    ] = RECEIVER_PORT,
    receiver_url: Annotated[
        str | None,
        typer.Option(
            envvar="EKB_RECEIVER_URL",
            help="The receiver's address as the docs tool reaches it "
            "(default http://127.0.0.1:<receiver port>; in Compose http://connector:8090).",
        ),
    ] = None,
    interval: Interval = 3.0,
    listing_every: ListingEvery = 10,
) -> None:
    """2 · Run the connectors as a service: every change goes on Kafka (S17)."""
    _logging()
    stop = threading.Event()
    stop_on_signals(stop)
    try:
        write = choose(load_settings(), offline=True, docker=True)
        with ExitStack() as stack:
            docs = stack.enter_context(httpx.Client(base_url=docs_url, timeout=10.0))
            hr = stack.enter_context(httpx.Client(base_url=hr_url, timeout=10.0))
            idp = stack.enter_context(httpx.Client(base_url=idp_url, timeout=10.0))
            service = ConnectService(docs, hr, idp, hr_db, write)
            receiver = create_receiver_app(service.connectors.webhook.receive)
            stack.enter_context(serving(receiver, host=bind, port=receiver_port))
            target = (receiver_url or f"http://127.0.0.1:{receiver_port}") + DOCS_WEBHOOK_PATH
            hook = call(docs, "POST", "/subscriptions", json={"url": target})
            stack.callback(lambda: call(docs, "DELETE", f"/subscriptions/{hook['id']}"))
            typer.echo("ekb connect: six connectors, publishing to")
            for step in ("bus", "registry", "rawstore"):
                typer.echo(f"  {write[step].describe()}")
            typer.echo(f"  webhooks from the docs tool to {target}")
            typer.echo(f"  a round every {interval:g}s, the full listings every {listing_every}")
            run_rounds(
                service.round,
                interval_s=interval,
                listing_every=listing_every,
                stop=stop,
                name="connect",
            )
    except SharedInfrastructureError as exc:
        typer.secho(f"ekb connect cannot start: {exc}", fg="red", err=True)
        raise typer.Exit(code=2) from exc
    except (InfrastructureError, httpx.HTTPError, OSError, LookupError, ValueError) as exc:
        hint = f" ({START_HINT})" if isinstance(exc, InfrastructureError) else ""
        typer.secho(f"ekb connect could not start: {exc}{hint}", fg="red", err=True)
        raise typer.Exit(code=1) from exc


def worker(
    models: Annotated[
        bool,
        typer.Option(
            "--models",
            envvar="EKB_MODELS",
            help="Call the models the profile names (T4, T5, T7); offline otherwise.",
        ),
    ] = False,
    interval: Interval = 1.0,
    listing_every: ListingEvery = 30,
) -> None:
    """4 · Run the worker as a service: T2 … T8 on Kafka, into OpenSearch and DynamoDB (S17)."""
    _logging()
    stop = threading.Event()
    stop_on_signals(stop)
    try:
        write, gateway, note = demo_choices(load_settings(), models, docker=True)
        if note:
            typer.secho(f"Note: {note}.", fg="yellow")
        service = WorkerService(write, gateway)
        typer.echo("ekb worker: group worker, T2 … T8")
        for choice in write.steps:
            typer.echo(f"  {choice.describe()}")
        run_rounds(
            service.round,
            interval_s=interval,
            listing_every=listing_every,
            stop=stop,
            name="worker",
        )
    except SharedInfrastructureError as exc:
        typer.secho(f"ekb worker cannot start: {exc}", fg="red", err=True)
        raise typer.Exit(code=2) from exc
    except (InfrastructureError, GatewayError, OSError, LookupError, ValueError) as exc:
        hint = f" ({START_HINT})" if isinstance(exc, InfrastructureError) else ""
        typer.secho(f"ekb worker could not start: {exc}{hint}", fg="red", err=True)
        raise typer.Exit(code=1) from exc
