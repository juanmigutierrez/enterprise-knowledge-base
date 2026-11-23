"""A change committed as a feed read releases its lock must survive the next poll."""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sources.mock_docs.app import create_app as docs_app
from sources.mock_idp.app import create_app as idp_app


@pytest.mark.parametrize(
    ("factory", "endpoint", "field", "write", "expected"),
    [
        (docs_app, "/changes", "changes", lambda s: s.delete("HR-PL-99"), "page.deleted"),
        (idp_app, "/events", "events", lambda s: s.deactivate("alice"), "user.deactivated"),
    ],
)
def test_write_at_read_boundary_is_delivered(
    factory: Callable[[], FastAPI],
    endpoint: str,
    field: str,
    write: Callable[[Any], object],
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = factory()
    store = app.state.store
    original_lock = store._lock
    with TestClient(app) as client:
        start = client.get(endpoint).json()["cursor"]

        class WriteOnRelease:
            def __enter__(self) -> None:
                original_lock.acquire()

            def __exit__(self, *args: object) -> None:
                original_lock.release()
                store._lock = original_lock
                write(store)

        monkeypatch.setattr(store, "_lock", WriteOnRelease())
        first = client.get(endpoint, params={"since": start}).json()
        second = client.get(endpoint, params={"since": first["cursor"]}).json()
        assert [event["event"] for event in first[field] + second[field]] == [expected]
