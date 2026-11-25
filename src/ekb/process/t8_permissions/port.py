"""Port for T8 · Permissions. Every resolving option implements :class:`PermissionResolver`.

A resolver reads one change event's readers (``ChangeEvent.acl``, already in
canonical user and group IDs: T1 mapped the users, D3) and the memberships
known right now, and decides **which readers go onto the document's chunks**.
It also says what it noticed: groups the identity provider doesn't know, users
it has no record of, users who have left.

Like T5's classifier and T6's linker, it returns a verdict, not chunks. The
step (:class:`~ekb.process.t8_permissions.step.PermissionStep`) puts the
readers on the chunks, checks the publication gate and publishes.

**Readers come from the source, never from the text.** T5 labels the
sensitivity from the content (S8) and T8 does not change who may read because
of it: rules such as "this agent never reads confidential HR data" are Cedar
policies, applied when someone asks (Q2, S13 and S15). T8 only requires the
label (the gate).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ekb.contracts import Acl, ChangeEvent, Membership


@runtime_checkable
class MembershipDirectory(Protocol):
    """Read access to who is in which group right now: all a resolver needs from the store."""

    def membership(self, user_id: str) -> Membership | None: ...

    def memberships(self) -> tuple[Membership, ...]: ...


@dataclass(frozen=True)
class Resolution:
    """The readers for one document's chunks, and what the resolver noticed about them."""

    acl: Acl
    resolver_id: str
    """For ``Chunk.resolver_id``: ``source_acl@1``."""
    unknown_groups: tuple[str, ...] = ()
    """Groups no identity user is in: they admit nobody until someone joins."""
    unknown_users: tuple[str, ...] = ()
    """Users named in the readers with no identity record: they read nothing."""
    inactive_users: tuple[str, ...] = ()
    """Users named in the readers who have left: they read nothing."""
    note: str = ""
    """In plain words: what was copied, and anything worth a look."""


@runtime_checkable
class PermissionResolver(Protocol):
    """Decides the readers of one document's chunks from its change event."""

    @property
    def resolver_id(self) -> str:
        """The option and its version, e.g. ``source_acl@1``."""
        ...

    def resolve(self, event: ChangeEvent, directory: MembershipDirectory) -> Resolution: ...
