"""The ``ekb demo`` command group that every step module adds a command to."""

from __future__ import annotations

import typer

demo_app = typer.Typer(
    no_args_is_help=True,
    help="Run one build step on the synthetic data and explain what happens.",
)
