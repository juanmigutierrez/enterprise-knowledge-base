"""Fixtures for the gate tests (S16): the scripted story on two profiles."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.gates.story import Story, built


@pytest.fixture(params=["ci", "local"])
def story(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Story]:
    """Every gate runs on the ``ci`` and the ``local`` profile: they hold whatever the
    options are."""
    with built(tmp_path, str(request.param)) as world:
        yield world
