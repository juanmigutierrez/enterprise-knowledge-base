"""``ekb`` command line: one container image, one entry point per service."""

from __future__ import annotations

import typer

from ekb.app.bootstrap import load_options
from ekb.app.demos import demo_app
from ekb.platform import registry
from ekb.platform.config import load_settings

app = typer.Typer(no_args_is_help=True, help="Enterprise knowledge base for AI agents.")
app.add_typer(demo_app, name="demo")


def _not_implemented(what: str, plan_task: str) -> None:
    typer.secho(f"{what} is not implemented yet (docs/plan.md {plan_task}).", fg="yellow")
    raise typer.Exit(code=2)


@app.command()
def options() -> None:
    """List configured steps, the chosen option, and whether it is implemented."""
    load_options()
    settings = load_settings()
    typer.echo(f"profile={settings.profile} provider={settings.provider}\n")
    for step, choice in sorted(settings.steps.items()):
        available = registry.options(step)
        status = "ready" if choice.option in available else "not built yet"
        card = f"  (card {choice.card})" if choice.card else ""
        typer.echo(f"{step:<16} {choice.option:<24} [{status}]{card}")
        if available:
            typer.echo(f"{'':<16} available: {', '.join(available)}")


@app.command()
def connect() -> None:
    """2 · Run the connectors and publish normalized change events (T1)."""
    _not_implemented("The connector service", "§4.2 task 5")


@app.command()
def worker() -> None:
    """4 · Consume change events and run T2-T8."""
    _not_implemented("The processing worker", "§4.2 task 6")


@app.command()
def api() -> None:
    """6 · Serve the Knowledge API as MCP tools (Q1-Q4)."""
    _not_implemented("The Knowledge API", "§4.2 task 8")


if __name__ == "__main__":
    app()
