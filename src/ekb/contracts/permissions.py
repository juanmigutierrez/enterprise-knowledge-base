"""Permission contract: who someone is to the knowledge base, and whom a chunk admits (S11).

Two halves meet here:

- **A chunk carries its readers** (``Chunk.acl``): canonical user and group IDs,
  copied from the source by T8. Groups stay groups (D8): the index holds
  ``it-employees``, not the people in it today.
- **A person's principals** are what a search filters on: their user ID and the
  groups they are in *right now*, from the identity provider (the permission
  store, S11). An inactive or unknown person has none: they read nothing,
  whatever groups they keep (S1c).

So a group change takes effect on the next request without rewriting a single
chunk, and :func:`admits` is the whole rule, in one place: for T8's "who can
read it now", the search store's filter and Q2's pre-filter (S13).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from ekb.contracts.events import Acl


class Principals(BaseModel):
    """What a search filters one person by: their user ID and their groups right now."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    groups: frozenset[str] = frozenset()


class Membership(BaseModel):
    """One identity user as the permission store keeps it: active or not, and their groups.

    Read from the identity provider's user record (``identity:users/alice``), so it
    is replaced whenever that record changes: a joiner, a leaver, a group change.
    """

    model_config = ConfigDict(frozen=True)

    user_id: str
    """The identity provider's user ID: the one canonical person ID (D3)."""
    active: bool
    groups: frozenset[str] = frozenset()
    """As the identity provider lists them. A leaver keeps theirs (S1c); they still read nothing."""
    source_key: str
    """The record it was read from, e.g. ``identity:users/alice``. Purging it removes the user."""
    original_lifetime: int = 0
    source_version: str

    @property
    def principals(self) -> Principals | None:
        """What this person reads with, or ``None`` when they are inactive: nothing at all."""
        if not self.active:
            return None
        return Principals(user_id=self.user_id, groups=self.groups)


def admits(acl: Acl, principals: Principals | None) -> bool:
    """Whether readers ``acl`` let these principals read. ``None`` (unknown, inactive) never.

    A person is admitted by name (``acl.users``) or through any group they are in
    now (``acl.groups``). An ACL that names nobody admits nobody.
    """
    if principals is None:
        return False
    return principals.user_id in acl.users or not acl.groups.isdisjoint(principals.groups)


def readers_now(acl: Acl, memberships: Iterable[Membership]) -> tuple[str, ...]:
    """The active users ``acl`` admits today, sorted: who could read it if they asked now.

    For people to look at (T8's result, the demo, the inspector), not for search:
    a search asks :func:`admits` for one person at a time.
    """
    return tuple(sorted(m.user_id for m in memberships if admits(acl, m.principals)))


class DocumentAcl(BaseModel):
    """The readers T8 last copied for one document, and the source version they came from.

    The permission store keeps one per document (D8: "document ACL versions"). With
    ``occurred_at`` it gives the permission sync lag (card 4.13): how long a
    revocation in the source took to reach the index.
    """

    model_config = ConfigDict(frozen=True)

    document_key: str
    acl: Acl
    acl_version: str
    """The source version the readers came from (``ChangeEvent.version``)."""
    occurred_at: datetime
    """When the source made the change that set them."""
    resolver_id: str
    """Which T8 option resolved them, e.g. ``source_acl@1``."""
