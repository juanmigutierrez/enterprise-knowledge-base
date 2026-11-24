"""Webhook push: the mock docs service tells subscribers about every change.

Real docs tools (SharePoint, Confluence) deliver webhooks this way: a
subscriber registers a URL, and after each change the tool POSTs a small
notification there. The notification says *that* a page changed, never its
new content, so a receiver must fetch the page.

Delivery is deliberately best-effort, as in the real tools:

- one POST per change and per subscriber, right after the change is saved;
- no retry and no queue: if the receiver is down, that notification is lost;
- a failed delivery never fails the edit that caused it.

Losing notifications is the point: it is why the connector also runs a full
listing (D3), and the demo for S2 shows one being caught that way.
No request signing: the mock binds to 127.0.0.1 only (a stated limit).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from sources.mock_docs.models import SourceChange

Post = Callable[[str, dict[str, Any]], None]

DELIVERY_TIMEOUT_S = 2.0


def http_post(url: str, payload: dict[str, Any]) -> None:
    """POST ``payload`` as JSON; any transport error or error status raises."""
    response = httpx.post(url, json=payload, timeout=DELIVERY_TIMEOUT_S)
    response.raise_for_status()


class UnknownSubscriptionError(KeyError):
    """No subscription with this ID."""


class SubscriptionCreate(BaseModel):
    """Request body to subscribe a receiver URL."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = Field(pattern=r"^https?://")


class Subscription(BaseModel):
    """A receiver and how its deliveries went (what a tool's admin page shows)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    url: str
    delivered: int = 0
    failed: int = 0
    last_error: str | None = None


class WebhookPublisher:
    """Keeps the subscriptions and delivers each change to all of them.

    ``post`` is injectable so tests can capture deliveries without a server.
    """

    def __init__(self, post: Post = http_post) -> None:
        self._post = post
        self._subscriptions: dict[int, Subscription] = {}
        self._next_id = 1
        self._lock = threading.Lock()

    def subscribe(self, url: str) -> Subscription:
        with self._lock:
            subscription = Subscription(id=self._next_id, url=url)
            self._subscriptions[subscription.id] = subscription
            self._next_id += 1
            return subscription

    def unsubscribe(self, subscription_id: int) -> None:
        with self._lock:
            if self._subscriptions.pop(subscription_id, None) is None:
                raise UnknownSubscriptionError(f"subscription {subscription_id} not found")

    def subscriptions(self) -> list[Subscription]:
        with self._lock:
            return sorted(self._subscriptions.values(), key=lambda s: s.id)

    def publish(self, change: SourceChange) -> None:
        """Deliver ``change`` to every subscriber; record, never raise, failures."""
        payload = change.model_dump(mode="json")
        for subscription in self.subscriptions():
            try:
                self._post(subscription.url, payload)
            except httpx.HTTPError as exc:
                self._record(subscription.id, error=f"{type(exc).__name__}: {exc}")
            else:
                self._record(subscription.id, error=None)

    def _record(self, subscription_id: int, *, error: str | None) -> None:
        with self._lock:
            current = self._subscriptions.get(subscription_id)
            if current is None:  # unsubscribed while delivering
                return
            if error is None:
                update: dict[str, Any] = {"delivered": current.delivered + 1}
            else:
                update = {"failed": current.failed + 1, "last_error": error}
            self._subscriptions[subscription_id] = current.model_copy(update=update)
