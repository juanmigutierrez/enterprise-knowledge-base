"""Webhook push (S2): every change is delivered, best-effort, without content."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sources.mock_docs.app import create_app
from sources.mock_docs.models import PageAcl, PageEdit
from sources.mock_docs.store import DocsStore
from sources.mock_docs.webhooks import UnknownSubscriptionError, WebhookPublisher

from tests.sources.conftest import TickingClock

RECEIVER = "http://127.0.0.1:9/webhooks/docs"


class Captured:
    """Stands in for the HTTP POST: records deliveries, or fails them on demand."""

    def __init__(self) -> None:
        self.deliveries: list[tuple[str, dict[str, Any]]] = []
        self.down = False

    def __call__(self, url: str, payload: dict[str, Any]) -> None:
        if self.down:
            raise httpx.ConnectError("receiver is down")
        self.deliveries.append((url, payload))


@pytest.fixture
def captured() -> Captured:
    return Captured()


@pytest.fixture
def client(captured: Captured) -> TestClient:
    return TestClient(create_app(post=captured))


def test_every_kind_of_change_is_pushed_in_order(client: TestClient, captured: Captured) -> None:
    subscription = client.post("/subscriptions", json={"url": RECEIVER}).json()
    assert subscription == {
        "id": 1,
        "url": RECEIVER,
        "delivered": 0,
        "failed": 0,
        "last_error": None,
    }
    new_page = {
        "id": "HR-PL-11",
        "title": "Wellbeing allowance",
        "language": "en",
        "department": "people",
        "body": "# Wellbeing allowance\n",
        "acl": {"groups": ["all-staff"]},
    }
    client.post("/pages", json=new_page)
    client.put("/pages/HR-PL-11", json={"body": "# Wellbeing allowance\nEUR 300 a year.\n"})
    client.put("/pages/HR-PL-11/acl", json={"groups": ["it-employees"]})
    client.delete("/pages/HR-PL-11")

    assert [url for url, _ in captured.deliveries] == [RECEIVER] * 4
    payloads = [payload for _, payload in captured.deliveries]
    assert [p["event"] for p in payloads] == [
        "page.created",
        "page.updated",
        "page.permissions_changed",
        "page.deleted",
    ]
    assert [p["seq"] for p in payloads] == sorted(p["seq"] for p in payloads)
    assert all("body" not in p and "acl" not in p for p in payloads)  # notification only
    # The webhook carries exactly what the feed holds.
    feed = client.get("/changes", params={"since": payloads[0]["seq"] - 1}).json()["changes"]
    assert feed == payloads
    assert client.get("/subscriptions").json()[0]["delivered"] == 4


def test_a_failed_delivery_does_not_fail_the_edit_and_is_not_retried(
    client: TestClient, captured: Captured
) -> None:
    client.post("/subscriptions", json={"url": RECEIVER})
    captured.down = True
    response = client.delete("/pages/HR-PL-99")
    assert response.status_code == 200
    assert client.get("/pages/HR-PL-99").status_code == 404

    captured.down = False
    client.put("/pages/HR-PL-04", json={"body": "edited\n"})
    events = [payload["event"] for _, payload in captured.deliveries]
    assert events == ["page.updated"]  # the lost delete is not re-sent
    status = client.get("/subscriptions").json()[0]
    assert (status["delivered"], status["failed"]) == (1, 1)
    assert "receiver is down" in status["last_error"]


def test_no_subscribers_no_deliveries_and_seeding_sends_nothing(
    client: TestClient, captured: Captured
) -> None:
    client.put("/pages/HR-PL-04", json={"body": "edited\n"})
    client.post("/subscriptions", json={"url": RECEIVER})
    client.post("/reset")
    assert captured.deliveries == []


def test_subscriptions_survive_a_reset(client: TestClient, captured: Captured) -> None:
    client.post("/subscriptions", json={"url": RECEIVER})
    client.post("/reset")
    client.delete("/pages/HR-PL-99")
    assert [payload["page_id"] for _, payload in captured.deliveries] == ["HR-PL-99"]


def test_unsubscribe_stops_deliveries(client: TestClient, captured: Captured) -> None:
    subscription = client.post("/subscriptions", json={"url": RECEIVER}).json()
    assert client.delete(f"/subscriptions/{subscription['id']}").status_code == 204
    assert client.delete(f"/subscriptions/{subscription['id']}").status_code == 404
    client.delete("/pages/HR-PL-99")
    assert captured.deliveries == []


def test_subscription_url_must_be_http(client: TestClient) -> None:
    assert client.post("/subscriptions", json={"url": "ftp://x"}).status_code == 422


def test_listeners_run_after_the_lock_is_released(ticking_clock: TickingClock) -> None:
    """A listener that reads the store (as a receiver fetching the page does) must not hang."""
    store = DocsStore.from_seed(clock=ticking_clock)
    seen: list[int] = []
    store.add_listener(lambda change: seen.append(store.get(change.page_id).rev))
    store.edit("HR-PL-04", PageEdit(body="edited\n"))
    store.set_acl("HR-PL-04", PageAcl(groups=("all-staff",)))
    assert seen == [2, 2]


def test_publisher_ignores_a_subscription_removed_during_delivery() -> None:
    publisher = WebhookPublisher(post=lambda url, payload: None)
    subscription = publisher.subscribe(RECEIVER)
    publisher.unsubscribe(subscription.id)
    publisher._record(subscription.id, error=None)  # must not resurrect it
    assert publisher.subscriptions() == []
    with pytest.raises(UnknownSubscriptionError):
        publisher.unsubscribe(subscription.id)
