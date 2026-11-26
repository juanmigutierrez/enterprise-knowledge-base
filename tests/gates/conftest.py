"""Fixtures for the gate tests (S16, S17): the scripted story wherever it can run."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from ekb.app.infra import drop
from ekb.platform.infra import new_namespace
from tests.fakes.infra import fake_infrastructure
from tests.gates.story import Story, built

PLACES = [
    "ci",
    "local",
    "local+fakes",
    pytest.param("local+docker", marks=pytest.mark.integration),
]
"""``ci`` and ``local`` in memory; ``local+fakes``: Kafka, DynamoDB, S3 and OpenSearch
code over fakes (every pull request); ``local+docker``: against ``docker compose``."""


@pytest.fixture(params=PLACES)
def story(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Story]:
    """Every gate runs in each place: they hold whatever the options are."""
    place = str(request.param)
    profile, _, infra = place.partition("+")
    if infra == "fakes":
        with fake_infrastructure(monkeypatch), built(tmp_path, profile, docker=True) as world:
            yield world
    elif infra == "docker":
        namespace = new_namespace("gate")
        try:
            with built(tmp_path, profile, docker=True, namespace=namespace) as world:
                yield world
        finally:
            drop(namespace)
    else:
        with built(tmp_path, profile) as world:
            yield world
