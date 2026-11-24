"""Webhook connector for the docs tool: the push half of D3.

The source POSTs a notification on every change (see ``receiver.py``). The
connector does two things, at two moments, the way production webhook
receivers do:

1. :meth:`receive` only checks the notification and queues it. A receiver must
   answer fast: a tool that waits too long for the reply treats the delivery as
   failed.
2. :meth:`sync` works through the queue: it fetches each page *now*, normalizes
   it (T1) and emits the event. A notification stays queued until its event is
   emitted, so a source outage while fetching loses nothing.

Every person ID goes through the :class:`PersonMap`: the writer becomes
``changed_by`` and the ACL keeps only users the identity provider knows.

What it cannot do is notice a notification that never arrived. That is the full
listing's job (``docs_full_listing``).
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Mapping

from ekb.connect.docs_api import DocsApi, DocsNotification
from ekb.connect.normalize import docs as normalize
from ekb.connect.people import PersonMap
from ekb.connect.port import EventSink, OriginalStore
from ekb.contracts import ChangeEvent, ChangeOp
from ekb.contracts.listing import ListingVersion
from ekb.platform.registry import register


@register("connect", "docs_webhook")
class DocsWebhookConnector:
    def __init__(
        self,
        api: DocsApi,
        sink: EventSink,
        store_original: OriginalStore,
        people: PersonMap,
        source: str = "hr_docs",
        *,
        known: Callable[[], Mapping[str, ListingVersion]] = dict,
    ) -> None:
        self._api = api
        self._sink = sink
        self._store_original = store_original
        self._people = people
        self._source = source
        self._inbox: deque[DocsNotification] = deque()
        self._lock = threading.Lock()
        self._drain_lock = threading.Lock()
        self._known = known

    @property
    def source(self) -> str:
        return self._source

    @property
    def pending(self) -> int:
        """Notifications received but not yet turned into events."""
        with self._lock:
            return len(self._inbox)

    def receive(self, notification: DocsNotification) -> None:
        """Queue a notification. An unknown change kind is refused right away,
        so the sender sees the delivery fail instead of the change vanishing."""
        normalize.op_for(notification.event)
        with self._lock:
            self._inbox.append(notification)

    def sync(self) -> int:
        with self._drain_lock:
            return self._drain()

    def _drain(self) -> int:
        emitted = 0
        while True:
            with self._lock:
                if not self._inbox:
                    return emitted
                notification = self._inbox[0]
            self._sink(self._normalize(notification))
            with self._lock:
                self._inbox.popleft()
            emitted += 1

    def _normalize(self, notification: DocsNotification) -> ChangeEvent:
        def gone(changed_by: str | None) -> ChangeEvent:
            return normalize.delete_event(
                self._source,
                notification.page_id,
                version=normalize.version_of(notification.rev, notification.acl_rev),
                occurred_at=notification.at,
                changed_by=changed_by,
            )

        op = normalize.op_for(notification.event)
        if op is ChangeOp.DELETE:
            return gone(self._people.user(notification.actor))
        fetched = self._api.get_page(notification.page_id)
        if fetched is None:  # deleted after this notification was sent; by whom is unknown
            return gone(None)
        page = fetched.page
        acl = normalize.acl_of(page.acl, users=self._people.users_in(page.acl.users))
        changed_by = self._people.user(page.modified_by)
        if op is ChangeOp.ACL_CHANGE:
            version_hint = self._known().get(page.id)
            known = normalize.parse_version(version_hint) if isinstance(version_hint, str) else None
            if known and known[0] == page.rev:
                return normalize.acl_change_event(
                    self._source, page, acl=acl, changed_by=changed_by
                )
        version = normalize.version_of(page.rev, page.acl_rev)
        pointer = self._store_original(f"{self._source}:{page.id}", version, fetched.original)
        return normalize.upsert_event(
            self._source, page, raw_pointer=pointer, acl=acl, changed_by=changed_by
        )
