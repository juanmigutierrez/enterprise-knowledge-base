"""The ``ekb demo`` command group that every step module adds a command to."""

from __future__ import annotations

from typing import Annotated

import typer

demo_app = typer.Typer(
    no_args_is_help=True,
    help="Run one build step on the synthetic data and explain what happens.",
)

DockerFlag = Annotated[
    bool,
    typer.Option(
        "--docker",
        help="Run the change log and the stores on Kafka, DynamoDB, S3 and OpenSearch "
        "(make up first), in a namespace of this run's own, dropped at the end (S17).",
    ),
]
"""The ``--docker`` flag of the demos that build the whole write path (S17)."""
