"""In-memory permission store: memberships by user, readers records by document.

It stands in for DynamoDB (production, and DynamoDB Local: ``dynamodb``, S17): one item
per user and one per document, plus an index from the identity record to its
user (a DynamoDB secondary index later). Thread-safe, lost when the process
stops.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from ekb.contracts import DocumentAcl, Membership, Principals
from ekb.platform.registry import register


@register("permissions", "in_memory")
class InMemoryPermissionStore:
    def __init__(self, current_lifetime: Callable[[str], int] | None = None) -> None:
        self._current_lifetime = current_lifetime
        self._users: dict[str, Membership] = {}
        self._by_source: dict[str, str] = {}  # record key -> user ID
        self._readers: dict[str, DocumentAcl] = {}
        self._lock = threading.Lock()
        self._hidden: set[str] = set()

    def set_visible(self, document_key: str, *, visible: bool) -> None:
        with self._lock:
            if visible:
                self._hidden.discard(document_key)
            else:
                self._hidden.add(document_key)

    def put_membership(self, membership: Membership) -> Membership | None:
        with self._lock:
            before = self._pop_source(membership.source_key)
            holder = self._users.get(membership.user_id)
            if holder is not None and holder.source_key != membership.source_key:
                # Another record defined this user before: the newer record owns it now.
                self._by_source.pop(holder.source_key, None)
                before = before or holder
            self._users[membership.user_id] = membership
            self._by_source[membership.source_key] = membership.user_id
            return before

    def remove_membership(self, source_key: str) -> Membership | None:
        with self._lock:
            return self._pop_source(source_key)

    def membership(self, user_id: str) -> Membership | None:
        with self._lock:
            member = self._users.get(user_id)
            return member if member is not None and self._live(member) else None

    def memberships(self) -> tuple[Membership, ...]:
        with self._lock:
            return tuple(m for _, m in sorted(self._users.items()) if self._live(m))

    def _live(self, member: Membership) -> bool:
        return member.source_key not in self._hidden and (
            self._current_lifetime is None
            or self._current_lifetime(member.source_key) == member.original_lifetime
        )

    def principals(self, user_id: str) -> Principals | None:
        membership = self.membership(user_id)
        return membership.principals if membership is not None else None

    def put_readers(self, record: DocumentAcl) -> DocumentAcl | None:
        with self._lock:
            before = self._readers.get(record.document_key)
            self._readers[record.document_key] = record
            return before

    def readers(self, document_key: str) -> DocumentAcl | None:
        with self._lock:
            return self._readers.get(document_key)

    def all_readers(self) -> tuple[DocumentAcl, ...]:
        with self._lock:
            return tuple(self._readers[k] for k in sorted(self._readers))

    def forget_readers(self, document_key: str) -> DocumentAcl | None:
        with self._lock:
            return self._readers.pop(document_key, None)

    def _pop_source(self, source_key: str) -> Membership | None:
        user_id = self._by_source.pop(source_key, None)
        return self._users.pop(user_id, None) if user_id is not None else None
