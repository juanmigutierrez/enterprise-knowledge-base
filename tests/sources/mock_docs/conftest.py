from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sources.mock_docs.app import create_app
from sources.mock_docs.store import DocsStore

from tests.sources.conftest import TickingClock


@pytest.fixture
def store(ticking_clock: TickingClock) -> DocsStore:
    return DocsStore.from_seed(clock=ticking_clock)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client
