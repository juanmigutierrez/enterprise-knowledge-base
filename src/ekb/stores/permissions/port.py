"""Port for 5 · Permission store. Every option implements :class:`PermissionStore`.

It holds two things the search index does not:

- **Memberships**, one per identity user: active or not, and the groups they
  are in now. They come from the identity provider's user records through the
  change log (``ekb.process.t8_permissions.sync``), so a joiner, a leaver or a
  group change reaches it like any other change. A search turns one person into
  their :meth:`~PermissionStore.principals`; an unknown or inactive person gets
  ``None`` and reads nothing.
- **Document readers**, one :class:`~ekb.contracts.DocumentAcl` per document:
  what T8 last copied, from which source version and when. It is the record the
  fast lane compares with, the one the inspector shows, and the start of the
  permission sync lag (card 4.13).

**One record defines one user.** ``put_membership`` replaces the user's
previous membership; ``remove_membership`` takes the record's key, which is
all a delete event carries (as in the entity store).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ekb.contracts import DocumentAcl, Membership, Principals


@runtime_checkable
class PermissionStore(Protocol):
    """Who is in which group now, and the readers T8 last copied per document."""

    # ------------------------------------------------------------ memberships

    def set_visible(self, document_key: str, *, visible: bool) -> None:
        """Hide an item during recovery, or release its completed state to readers."""
        ...

    def put_membership(self, membership: Membership) -> Membership | None:
        """Add or replace a user's membership; return the one before, if any."""
        ...

    def remove_membership(self, source_key: str) -> Membership | None:
        """Remove the user this record defined (a purge); return them, if there was one."""
        ...

    def membership(self, user_id: str) -> Membership | None: ...

    def memberships(self) -> tuple[Membership, ...]:
        """Every user, active or not, sorted by user ID."""
        ...

    def principals(self, user_id: str) -> Principals | None:
        """What ``user_id`` reads with now; ``None`` if unknown or inactive: nothing."""
        ...

    # ------------------------------------------------------- document readers

    def put_readers(self, record: DocumentAcl) -> DocumentAcl | None:
        """Record a document's readers; return the record before, if any."""
        ...

    def readers(self, document_key: str) -> DocumentAcl | None: ...

    def all_readers(self) -> tuple[DocumentAcl, ...]:
        """Every document's readers record, sorted by document key."""
        ...

    def forget_readers(self, document_key: str) -> DocumentAcl | None:
        """A purged document's readers record goes; returns it, if there was one."""
        ...
