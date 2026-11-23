"""Every registered option must satisfy its step's port.

When a new option is added under ``<step>/options/`` this test picks it up
automatically. When a new port is written, add it to ``PORTS``.
"""

from __future__ import annotations

import pytest

from ekb.app.bootstrap import load_options
from ekb.platform import registry
from ekb.process.t7_embed.port import Embedder

PORTS: dict[str, type] = {
    "t7_embed": Embedder,
}

# Params needed to build an option without external services, if any.
TEST_PARAMS: dict[tuple[str, str], dict[str, object]] = {}

load_options()
CASES = [(step, name) for step in PORTS for name in registry.options(step)]


@pytest.mark.parametrize(("step", "name"), CASES, ids=[f"{s}:{n}" for s, n in CASES])
def test_option_implements_port(step: str, name: str) -> None:
    instance = registry.create(step, name, **TEST_PARAMS.get((step, name), {}))
    assert isinstance(instance, PORTS[step])
