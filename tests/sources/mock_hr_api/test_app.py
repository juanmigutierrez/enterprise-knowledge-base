"""The HR record API over HTTP: what the connector (S2a) will see."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sources.mock_hr_api.app import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def test_listing_carries_as_of(client: TestClient) -> None:
    body = client.get("/employees").json()
    assert len(body["records"]) == 12
    assert body["as_of"]


def test_delta_through_the_query_string(client: TestClient) -> None:
    as_of = client.get("/employees").json()["as_of"]
    changed = client.patch("/employees/E-1008", json={"office": "Milan"})
    assert changed.status_code == 200
    delta = client.get("/employees", params={"updated_since": as_of}).json()
    assert [e["id"] for e in delta["records"]] == ["E-1008"]


def test_delete_is_204_and_silent(client: TestClient) -> None:
    as_of = client.get("/employees").json()["as_of"]
    assert client.delete("/employees/E-1012").status_code == 204
    assert client.get("/employees", params={"updated_since": as_of}).json()["records"] == []
    assert client.get("/employees/E-1012").status_code == 404
    assert client.delete("/employees/E-1012").status_code == 404


def test_errors_map_to_status_codes(client: TestClient) -> None:
    assert client.delete("/employees/E-1004").status_code == 409  # still manages people
    assert client.patch("/employees/E-1001", json={"team_id": "T-XXX"}).status_code == 422
    assert client.patch("/employees/E-1001", json={"salary": 1}).status_code == 422  # no such field
    assert client.get("/employees", params={"updated_since": "yesterday"}).status_code == 422
    assert "not found" in client.get("/teams/T-XXX").json()["detail"]


def test_hire_and_teams(client: TestClient) -> None:
    hire = {
        "id": "E-1013",
        "name": "Sofia Marino",
        "work_email": "sofia.marino@example.com",
        "team_id": "T-PRD",
        "office": "Toronto",
        "manager_id": "E-1010",
        "start_date": "2026-12-01",
    }
    assert client.post("/employees", json=hire).status_code == 201
    assert client.post("/employees", json=hire).status_code == 409
    assert client.patch("/teams/T-PRD", json={"lead_id": "E-1013"}).json()["lead_id"] == "E-1013"
    assert client.get("/teams/T-PRD").json()["lead_id"] == "E-1013"
    assert len(client.get("/teams").json()["records"]) == 5


def test_reset(client: TestClient) -> None:
    client.delete("/employees/E-1012")
    assert client.post("/reset").json() == {"employees": 12, "teams": 5}
