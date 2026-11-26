"""CLI for local and explicitly enabled hosted experiments."""

from pathlib import Path

import typer
import yaml

from ekb.evaluation.runner import run_card

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Compare options on the frozen synthetic HR corpus."""


@app.command()
def run(
    experiment: Path, summary: Path | None = None, holdout: bool = False, paid: bool = False
) -> None:
    """Run a card. Reserve --holdout for the final comparison."""
    try:
        report, passed = run_card(experiment, holdout=holdout, paid=paid)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    body = report.read_text(encoding="utf8")
    if summary:
        with summary.open("a", encoding="utf8") as output:
            output.write(body)
    typer.echo(body)
    typer.echo(str(report))
    if not passed:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
