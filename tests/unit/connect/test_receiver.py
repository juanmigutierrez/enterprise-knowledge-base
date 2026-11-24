"""The webhook receiver: accept fast, refuse what it cannot understand."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ekb.connect.docs_api import DocsNotification
from ekb.connect.normalize.docs import op_for
from ekb.connect.receiver import DOCS_WEBHOOK_PATH, create_receiver_app

NOTIFICATION = {
    "seq": 7,
    "event": "page.updated",
    "page_id": "HR-PL-04",
    "rev": 2,
    "acl_rev": 1,
    "at": "2026-11-23T09:00:00Z",
    "added_by_a_newer_source_version": True,  # unknown fields are ignored
}


def _receiver() -> tuple[TestClient, list[DocsNotification]]:
    received: list[DocsNotification] = []

    def on_notification(notification: DocsNotification) -> None:
        op_for(notification.event)  # as the webhook connector does
        received.append(notification)

    return TestClient(create_receiver_app(on_notification)), received


def test_a_notification_is_accepted_and_handed_on() -> None:
    client, received = _receiver()
    response = client.post(DOCS_WEBHOOK_PATH, json=NOTIFICATION)
    assert response.status_code == 202
    assert [(n.seq, n.page_id) for n in received] == [(7, "HR-PL-04")]
    assert client.get("/health").json() == {"status": "ok"}


def test_an_unknown_kind_fails_the_delivery() -> None:
    client, received = _receiver()
    response = client.post(DOCS_WEBHOOK_PATH, json={**NOTIFICATION, "event": "page.moved"})
    assert response.status_code == 422
    assert "page.moved" in response.json()["detail"]
    assert received == []


def test_a_malformed_body_is_refused() -> None:
    client, received = _receiver()
    assert client.post(DOCS_WEBHOOK_PATH, json={"page_id": "HR-PL-04"}).status_code == 422
    assert received == []
