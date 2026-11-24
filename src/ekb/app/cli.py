"""``ekb`` command line: one container image, one entry point per service."""

from __future__ import annotations

import os

import typer

from ekb.app.bootstrap import load_options
from ekb.app.demos import demo_app
from ekb.app.gateway import max_spend, provider_status
from ekb.app.inspector import inspector
from ekb.platform import registry
from ekb.platform.config import load_settings

app = typer.Typer(no_args_is_help=True, help="Enterprise knowledge base for AI agents.")
app.add_typer(demo_app, name="demo")
app.command()(inspector)


def _not_implemented(what: str, plan_task: str) -> None:
    typer.secho(f"{what} is not implemented yet (docs/plan.md {plan_task}).", fg="yellow")
    raise typer.Exit(code=2)


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


@app.command()
def connect() -> None:
    """2 Â· Run the connectors and publish normalized change events (T1)."""
    _not_implemented("The connector service", "Â§4.2 task 5")


@app.command()
def worker() -> None:
    """4 Â· Consume change events and run T2-T8."""
    _not_implemented("The processing worker", "Â§4.2 task 6")


@app.command()
def api() -> None:
    """6 Â· Serve the Knowledge API as MCP tools (Q1-Q4)."""
    _not_implemented("The Knowledge API", "Â§4.2 task 8")


if __name__ == "__main__":
    app()
