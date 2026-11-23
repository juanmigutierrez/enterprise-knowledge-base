"""The HTTP API is the only way the product sees the mock."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_list_and_get(client: TestClient) -> None:
    pages = client.get("/pages").json()
    assert len(pages) == 12
    assert "body" not in pages[0]
    page = client.get("/pages/HR-PL-01").json()
    assert page["body"].startswith("# Annual leave policy")


def test_missing_page_is_404(client: TestClient) -> None:
    response = client.get("/pages/HR-NOPE")
    assert response.status_code == 404
    assert "HR-NOPE" in response.json()["detail"]


def test_edit_acl_delete_and_feed(client: TestClient) -> None:
    cursor = client.get("/changes").json()["cursor"]
    edited = client.put("/pages/HR-PL-04", json={"body": "new\n"}).json()
    assert edited["rev"] == 2
    shared = client.put(
        "/pages/HR-PL-05/acl", json={"groups": ["es-employees", "it-employees"]}
    ).json()
    assert (shared["rev"], shared["acl_rev"]) == (1, 2)
    deleted = client.delete("/pages/HR-PL-99")
    assert deleted.json()["event"] == "page.deleted"
    assert client.get("/pages/HR-PL-99").status_code == 404
    assert client.delete("/pages/HR-PL-99").status_code == 404

    feed = client.get("/changes", params={"since": cursor}).json()
    assert [c["event"] for c in feed["changes"]] == [
        "page.updated",
        "page.permissions_changed",
        "page.deleted",
    ]
    assert feed["cursor"] == cursor + 3


def test_create_and_conflict(client: TestClient) -> None:
    page = {
        "id": "HR-PL-11",
        "title": "Volunteering days",
        "language": "en",
        "department": "people",
        "body": "Two paid volunteering days per year.\n",
        "acl": {"groups": ["all-staff"]},
    }
    created = client.post("/pages", json=page)
    assert created.status_code == 201
    assert created.json()["rev"] == 1
    assert client.post("/pages", json=page).status_code == 409


def test_bad_requests_are_rejected(client: TestClient) -> None:
    assert client.put("/pages/HR-PL-05/acl", json={"users": ["mallory"]}).status_code == 422
    assert client.put("/pages/HR-PL-05", json={"text": "wrong field"}).status_code == 422
    assert client.get("/changes", params={"since": -1}).status_code == 422


def test_reset_restores_the_seed(client: TestClient) -> None:
    client.delete("/pages/HR-PL-99")
    assert client.post("/reset").json() == {"pages": 12}
    assert client.get("/pages/HR-PL-99").status_code == 200
    assert client.get("/changes").json()["cursor"] == 12
