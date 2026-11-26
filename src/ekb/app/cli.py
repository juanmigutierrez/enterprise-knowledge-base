"""``ekb`` command line: one container image, one entry point per service."""

from __future__ import annotations

import os

import typer

from ekb.app.api import api
from ekb.app.bootstrap import load_options
from ekb.app.demos import demo_app
from ekb.app.gateway import max_spend, provider_status
from ekb.app.inspector import inspector
from ekb.app.serve import connect, worker
from ekb.platform import registry
from ekb.platform.config import load_settings

app = typer.Typer(no_args_is_help=True, help="Enterprise knowledge base for AI agents.")
app.add_typer(demo_app, name="demo")
app.command()(inspector)
app.command()(api)
app.command()(connect)  # S17: the connectors as a service, on Kafka
app.command()(worker)  # S17: T2 ... T8 as a service


@app.command()
def options() -> None:
    """List configured steps, the chosen option, and whether it is implemented."""
    load_options()
    settings = load_settings()
    typer.echo(f"profile={settings.profile}\n")
    for step, choice in sorted(settings.steps.items()):
        available = registry.options(step)
        status = "ready" if choice.option in available else "not built yet"
        card = f"  (card {choice.card})" if choice.card else ""
        typer.echo(f"{step:<16} {choice.option:<24} [{status}]{card}")
        if available:
            typer.echo(f"{'':<16} available: {', '.join(available)}")

    gateway = settings.gateway
    typer.echo(
        f"\nmodel gateway (S7): cache={gateway.cache.option} cost_log={gateway.cost_log.option} "
        f"max_spend_usd={max_spend(gateway, os.environ):g}"
    )
    for provider in provider_status(gateway):
        key = (
            f"{provider.key_env} {'set' if provider.ready else 'NOT set'}"
            if provider.key_env
            else "no key needed"
        )
        typer.echo(f"  provider {provider.provider:<14} {key}")
    for task, chain in sorted(gateway.tasks.items()):
        typer.echo(f"  task {task:<18} {' -> '.join(chain)}")


if __name__ == "__main__":
    app()
