"""``ekb-eval``: run an option experiment and write a result summary.

An experiment file (``evaluation/experiments/*.yaml``) names one step, the
options to compare, the dataset, the gates and the primary metric. The runner
builds each option through the registry, runs the same frozen questions, and
writes ``evaluation/results/<date>-<card>.md`` from the results template.
"""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True, help="Run option experiments.")


@app.callback()
def main() -> None:
    """Option experiments (docs/option-evaluation.md)."""


@app.command()
def run(
    experiment: Path,
    summary: Path | None = typer.Option(None, help="Also append a Markdown summary here."),  # noqa: B008
) -> None:
    """Run one experiment file."""
    if not experiment.exists():
        typer.secho(f"experiment file not found: {experiment}", fg="red")
        raise typer.Exit(code=1)
    typer.secho(
        "The experiment runner is not implemented yet (docs/plan.md §4.2 task 10).", fg="yellow"
    )
    raise typer.Exit(code=2)
