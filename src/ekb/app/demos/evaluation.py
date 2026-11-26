"""Use the evaluation entry point so the demo exercises the same gates as a report."""

import subprocess  # nosec B404
import sys

import typer

from ekb.app.demos._app import demo_app


@demo_app.command("evaluation")
def evaluation() -> None:
    """Watch a stale permission copy and a warm answer cache after revocation."""
    result = subprocess.run(  # nosec B603: fixed local command
        [
            sys.executable,
            "-m",
            "ekb.evaluation.cli",
            "run",
            "evaluation/experiments/4.13-permissions.yaml",
        ],
        check=False,
    )
    if result.returncode:
        raise typer.Exit(result.returncode)
