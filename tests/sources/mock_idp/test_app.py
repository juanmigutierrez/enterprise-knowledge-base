"""The identity provider over HTTP, in SCIM vocabulary."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sources.mock_idp.app import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def test_scim_list_shape(client: TestClient) -> None:
    body = client.get("/scim/v2/Users").json()
    assert body["totalResults"] == 12
    alice = next(u for u in body["Resources"] if u["id"] == "alice")
    assert alice["userName"] == "alice"
    assert alice["displayName"] == "Alice Rossi"
    assert alice["active"] is True
    assert client.get("/scim/v2/Groups").json()["totalResults"] == 6


def test_membership_changes_and_events(client: TestClient) -> None:
    removed = client.delete("/scim/v2/Groups/it-employees/members/alice")
    assert "alice" not in removed.json()["members"]
    added = client.post("/scim/v2/Groups/hr-team/members", json={"user_id": "carla"})
    assert "carla" in added.json()["members"]
    feed = client.get("/events").json()
    assert [e["event"] for e in feed["events"]] == ["group.member_removed", "group.member_added"]
    assert feed["cursor"] == 2
    assert client.get("/events", params={"since": 1}).json()["events"][0]["user_id"] == "carla"


def test_lifecycle(client: TestClient) -> None:
    joiner = {"id": "sofia", "name": "Sofia Marino", "location": "Milan", "groups": ["all-staff"]}
    assert client.post("/scim/v2/Users", json=joiner).status_code == 201
    assert client.post("/scim/v2/Users", json=joiner).status_code == 409
    left = client.post("/scim/v2/Users/olivia/deactivate")
    assert left.json()["active"] is False
    assert client.post("/scim/v2/Users/olivia/deactivate").status_code == 409
    assert client.get("/scim/v2/Users/olivia").json()["active"] is False


def test_errors(client: TestClient) -> None:
    assert client.get("/scim/v2/Users/mallory").status_code == 404
    assert client.get("/scim/v2/Groups/admins").status_code == 404
    bad = {"id": "sofia", "name": "Sofia Marino", "location": "Milan", "groups": ["admins"]}
    assert client.post("/scim/v2/Users", json=bad).status_code == 422
    assert client.get("/events", params={"since": -1}).status_code == 422


def test_reset(client: TestClient) -> None:
    client.post("/scim/v2/Users/olivia/deactivate")
    assert client.post("/reset").json() == {"users": 12, "groups": 6}
    assert client.get("/events").json()["cursor"] == 0
