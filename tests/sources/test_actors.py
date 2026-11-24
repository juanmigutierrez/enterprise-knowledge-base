"""Who made a change (S2a): every mock records the writer, as an identity provider user ID."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sources.mock_docs.app import create_app as docs_app
from sources.mock_hr_api.app import create_app as hr_api_app
from sources.mock_idp.app import create_app as idp_app

DANA = {"X-Actor": "dana"}


def test_docs_keeps_the_writer_on_the_page_and_in_the_feed() -> None:
    client = TestClient(docs_app())
    cursor = client.get("/changes").json()["cursor"]
    edited = client.put("/pages/HR-PL-04", json={"body": "x\n"}, headers=DANA).json()
    assert edited["modified_by"] == "dana"
    shared = client.put(
        "/pages/HR-PL-05/acl", json={"groups": ["es-employees"]}, headers={"X-Actor": "lucia"}
    )
    assert shared.json()["modified_by"] == "lucia"
    client.delete("/pages/HR-PL-99", headers=DANA)
    client.put("/pages/HR-PL-01", json={"body": "y\n"})  # nobody named
    feed = client.get("/changes", params={"since": cursor}).json()["changes"]
    assert [c["actor"] for c in feed] == ["dana", "lucia", "dana", None]
    assert client.get("/pages/HR-PL-01").json()["modified_by"] is None
    assert client.get("/pages").json()[0]["modified_by"] is None  # seeded pages name nobody


def test_docs_refuses_an_unknown_writer_and_changes_nothing() -> None:
    client = TestClient(docs_app())
    response = client.put("/pages/HR-PL-04", json={"body": "x\n"}, headers={"X-Actor": "mallory"})
    assert response.status_code == 422
    assert client.get("/pages/HR-PL-04").json()["rev"] == 1


def test_hr_api_keeps_the_writer_as_updated_by() -> None:
    client = TestClient(hr_api_app())
    moved = client.patch("/employees/E-1008", json={"office": "Madrid"}, headers=DANA).json()
    assert moved["updated_by"] == "dana"
    team = client.patch("/teams/T-ENG", json={"name": "Engineering"}, headers=DANA).json()
    assert team["updated_by"] == "dana"
    again = client.patch("/employees/E-1008", json={"office": "Milan"}).json()
    assert again["updated_by"] is None  # each save names its own writer
    assert client.delete("/employees/E-1012", headers=DANA).status_code == 204


def test_hr_api_refuses_a_malformed_writer() -> None:
    client = TestClient(hr_api_app())
    response = client.patch(
        "/employees/E-1008", json={"office": "Madrid"}, headers={"X-Actor": "E-1001"}
    )
    assert response.status_code == 422


def test_idp_keeps_the_admin_on_each_event() -> None:
    client = TestClient(idp_app())
    client.delete("/scim/v2/Groups/it-employees/members/alice", headers={"X-Actor": "bob"})
    client.post("/scim/v2/Users/olivia/deactivate", headers=DANA)
    client.post(
        "/scim/v2/Users",
        json={"id": "sara", "name": "Sara Neri", "location": "Milan", "groups": ["all-staff"]},
    )
    events = client.get("/events").json()["events"]
    assert [(e["event"], e["actor"]) for e in events] == [
        ("group.member_removed", "bob"),
        ("user.deactivated", "dana"),
        ("user.created", None),
        ("group.member_added", None),
    ]


def test_idp_refuses_an_unknown_admin_and_records_nothing() -> None:
    client = TestClient(idp_app())
    response = client.delete(
        "/scim/v2/Groups/it-employees/members/alice", headers={"X-Actor": "mallory"}
    )
    assert response.status_code == 422
    assert client.get("/events").json()["events"] == []
    assert "it-employees" in client.get("/scim/v2/Users/alice").json()["groups"]
