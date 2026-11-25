"""Keeping the permission store's memberships current from identity events (S11).

The records path of the write path, as for the entity store (S9)::

    identity user event ─ T2 process ─▶ T3: "not a document" ─▶ handle(event): read, put
                        ─ T2 purge   ─────────────────────────▶ forget(key): remove

``handle`` reads the user record's stored original (the exact version the
event names) and puts the membership it defines. That is the whole of a group
change: no chunk is touched, and the next search for that person filters with
their new groups. A leaver is deactivated, not deleted (S1c): their membership
stays, inactive, and :func:`ekb.contracts.admits` gives them nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ekb.changelog.rawstore.port import RawStore
from ekb.contracts import ChangeEvent, ChangeOp, Membership
from ekb.process.t8_permissions.records import MembershipReader
from ekb.stores.permissions.port import PermissionStore


@dataclass(frozen=True)
class MembershipResult:
    """What one identity record event did to the permission store."""

    event: ChangeEvent
    membership: Membership
    replaced: Membership | None
    """The same user's membership before, if any."""
    reason: str


def change(before: Membership | None, after: Membership) -> str:
    """``alice: -it-employees`` · ``olivia left: reads nothing now``: the change in words."""
    who = after.user_id
    if before is None:
        state = "" if after.active else ", inactive"
        return f"{who} added{state}: {', '.join(sorted(after.groups)) or 'no groups'}"
    if before.active and not after.active:
        return f"{who} left: reads nothing now (keeps {len(after.groups)} groups)"
    if not before.active and after.active:
        return f"{who} is back: reads with {', '.join(sorted(after.groups)) or 'no groups'}"
    gained = [f"+{g}" for g in sorted(after.groups - before.groups)]
    lost = [f"-{g}" for g in sorted(before.groups - after.groups)]
    return f"{who}: {' '.join(gained + lost) or 'no change in groups'}"


class MembershipSync:
    """The permission store's memberships, fed by the sources that say who is in which group."""

    def __init__(
        self, store: PermissionStore, rawstore: RawStore, readers: Mapping[str, MembershipReader]
    ) -> None:
        self._store = store
        self._rawstore = rawstore
        self._readers = dict(readers)

    def handles(self, source: str) -> bool:
        """Whether ``source``'s records say who is in which group."""
        return source in self._readers

    def handle(self, event: ChangeEvent) -> MembershipResult:
        """Put the membership this upsert's record defines."""
        reader = self._readers.get(event.source)
        if reader is None:
            raise ValueError(f"{event.source} defines no memberships")
        if event.op is not ChangeOp.UPSERT or event.raw_pointer is None:
            raise ValueError(f"only an upsert defines a membership, not {event.op.value}")
        membership = reader(event, self._rawstore.get(event.raw_pointer))
        replaced = self._store.put_membership(membership)
        return MembershipResult(event, membership, replaced, change(replaced, membership))

    def forget(self, document_key: str) -> Membership | None:
        """A purged user record's membership leaves the store; returns it, if there was one."""
        return self._store.remove_membership(document_key)
