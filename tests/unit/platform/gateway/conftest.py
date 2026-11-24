"""Fixtures for the gateway tests; the helpers themselves are in ``helpers.py``."""

from __future__ import annotations

import pytest

from tests.unit.platform.gateway.helpers import World, make_world


@pytest.fixture
def world() -> World:
    return make_world()
