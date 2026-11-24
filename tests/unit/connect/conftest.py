"""Connector tests run against the real mocks, in-process.

A ``TestClient`` has the API of an ``httpx.Client`` (it is an ``httpx2`` one
since S7; see ``tests/http.py``), so the connector talks to the mock exactly as
it would over the network. Webhook delivery is captured instead of
POSTed, so each test decides when notifications reach the connector.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sources.mock_docs.app import create_app
from sources.mock_docs.store import DocsStore
from sources.mock_hr_api.app import create_app as hr_api_app
from sources.mock_idp.app import create_app as idp_app

from ekb.connect.docs_api import DocsApi, DocsNotification
from ekb.connect.hr_api import HrApi
from ekb.connect.identity_api import IdentityApi
from ekb.connect.people import PersonMap, load_people
from ekb.contracts import ChangeEvent
from tests.http import as_httpx

NEW_PAGE = {
    "id": "HR-PL-11",
    "title": "Wellbeing allowance",
    "language": "en",
    "department": "people",
    "body": "# Wellbeing allowance\nEUR 300 a year.\n",
    "acl": {"groups": ["all-staff"]},
}


class Originals:
    """In-memory original store: what the raw store (S3) will do for real."""

    def __init__(self) -> None:
        self.saved: dict[str, bytes] = {}

    def __call__(self, document_key: str, version: str, original: bytes) -> str:
        pointer = f"mem://raw/{document_key.replace(':', '/')}/{version}"
        self.saved[pointer] = original
        return pointer


class Webhooks:
    """Captured webhook deliveries, as the connector would receive them."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def __call__(self, url: str, payload: dict[str, Any]) -> None:
        self.payloads.append(payload)

    def take(self) -> list[DocsNotification]:
        taken = [DocsNotification.model_validate(p) for p in self.payloads]
        self.payloads.clear()
        return taken


@pytest.fixture
def webhooks() -> Webhooks:
    return Webhooks()


@pytest.fixture
def docs(webhooks: Webhooks) -> Iterator[TestClient]:
    with TestClient(create_app(post=webhooks)) as client:
        client.post("/subscriptions", json={"url": "http://127.0.0.1:9/webhooks/docs"})
        yield client


@pytest.fixture
def mock_store(docs: TestClient) -> DocsStore:
    """The mock's store, for changes a test needs to make between two calls."""
    store: DocsStore = docs.app.state.store  # type: ignore[attr-defined]
    return store


@pytest.fixture
def api(docs: TestClient) -> DocsApi:
    return DocsApi(as_httpx(docs))


@pytest.fixture
def originals() -> Originals:
    return Originals()


@pytest.fixture
def events() -> list[ChangeEvent]:
    return []


@pytest.fixture
def idp() -> Iterator[TestClient]:
    with TestClient(idp_app()) as client:
        yield client


@pytest.fixture
def hr() -> Iterator[TestClient]:
    with TestClient(hr_api_app()) as client:
        yield client


@pytest.fixture
def people(idp: TestClient, hr: TestClient) -> PersonMap:
    """The real mapping: identity provider users matched to HR employees by full name."""
    return PersonMap(load_people(IdentityApi(as_httpx(idp)), HrApi(as_httpx(hr))))
