"""State of the mock identity provider: users, group memberships and an event feed.

Rules later steps rely on:

- The feed starts empty. Seed users are the state at start-up, not events: an
  identity sync saves a cursor before the full SCIM listing and replays
  events from that cursor. That is how real event hooks work too.
- A leaver is **deactivated**, not deleted: the user stays listed with
  ``active: false`` and keeps their memberships. Permission resolution (S11)
  must treat an inactive user as having no access at all.
- Events are emitted only for real changes. Adding someone who is already a
  member, or removing someone who isn't, is accepted and does nothing, as SCIM
  servers do. (Unlike the docs mock, where every save counts.)
- A joiner produces ``user.created`` and one ``group.member_added`` per group,
  so a reader that follows only membership events still sees everything.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from sources.common.clock import Clock, utc_now
from sources.common.directory import DIRECTORY_FILE, Directory, UnknownPrincipalError
from sources.common.feed import ChangeFeed
from sources.mock_idp.models import (
    IdentityEvent,
    IdentityEventKind,
    ScimGroup,
    ScimUser,
    UserCreate,
)

__all__ = ["IdentityConflictError", "IdpStore", "NotFoundError", "UnknownPrincipalError"]


class NotFoundError(KeyError):
    """No user or group with this ID."""


class IdentityConflictError(ValueError):
    """The change clashes with the current state (duplicate user or name, already inactive)."""


@dataclass
class _User:
    name: str
    location: str
    active: bool = True


@dataclass
class _Group:
    description: str
    members: set[str] = field(default_factory=set)


class IdpStore:
    """Users and groups in memory, safe to use from several threads."""

    def __init__(self, directory: Directory, *, clock: Clock = utc_now) -> None:
        self._clock = clock
        self._users = {
            uid: _User(u.name, u.location, u.active) for uid, u in directory.users.items()
        }
        self._groups = {gid: _Group(desc) for gid, desc in directory.groups.items()}
        for uid, user in directory.users.items():
            for gid in user.groups:
                self._groups[gid].members.add(uid)
        self._feed: ChangeFeed[IdentityEvent] = ChangeFeed()
        self._lock = threading.Lock()

    @classmethod
    def from_seed(cls, path: Path = DIRECTORY_FILE, *, clock: Clock = utc_now) -> IdpStore:
        return cls(Directory.load(path), clock=clock)

    # ------------------------------------------------------------------ reads

    @property
    def cursor(self) -> int:
        with self._lock:
            return self._feed.cursor

    def users(self) -> list[ScimUser]:
        with self._lock:
            return [self._scim_user(uid) for uid in sorted(self._users)]

    def user(self, user_id: str) -> ScimUser:
        with self._lock:
            self._user(user_id)
            return self._scim_user(user_id)

    def groups(self) -> list[ScimGroup]:
        with self._lock:
            return [self._scim_group(gid) for gid in sorted(self._groups)]

    def group(self, group_id: str) -> ScimGroup:
        with self._lock:
            self._group(group_id)
            return self._scim_group(group_id)

    def events(self, since: int = 0) -> list[IdentityEvent]:
        with self._lock:
            return self._feed.since(since)

    def read_feed(self, since: int) -> tuple[list[IdentityEvent], int]:
        """Read entries and their resume cursor in the same snapshot."""
        with self._lock:
            return self._feed.since(since), self._feed.cursor

    # ----------------------------------------------------------------- writes

    def create_user(self, request: UserCreate) -> ScimUser:
        """A joiner. Refused as a whole if any group is unknown."""
        with self._lock:
            if request.id in self._users:
                raise IdentityConflictError(f"user {request.id} exists")
            if any(u.name == request.name for u in self._users.values()):
                raise IdentityConflictError(
                    f"name {request.name!r} is taken; names link to HR records 1-1"
                )
            unknown = sorted(set(request.groups) - set(self._groups))
            if unknown:
                raise UnknownPrincipalError(f"unknown groups {unknown}")
            self._users[request.id] = _User(request.name, request.location)
            self._record(IdentityEventKind.USER_CREATED, request.id)
            for gid in sorted(set(request.groups)):
                self._groups[gid].members.add(request.id)
                self._record(IdentityEventKind.MEMBER_ADDED, request.id, gid)
            return self._scim_user(request.id)

    def deactivate(self, user_id: str) -> ScimUser:
        """A leaver: can no longer sign in; stays listed with their memberships."""
        with self._lock:
            user = self._user(user_id)
            if not user.active:
                raise IdentityConflictError(f"user {user_id} is already inactive")
            user.active = False
            self._record(IdentityEventKind.USER_DEACTIVATED, user_id)
            return self._scim_user(user_id)

    def add_member(self, group_id: str, user_id: str) -> ScimGroup:
        with self._lock:
            group = self._group(group_id)
            self._user(user_id)
            if user_id not in group.members:
                group.members.add(user_id)
                self._record(IdentityEventKind.MEMBER_ADDED, user_id, group_id)
            return self._scim_group(group_id)

    def remove_member(self, group_id: str, user_id: str) -> ScimGroup:
        with self._lock:
            group = self._group(group_id)
            self._user(user_id)
            if user_id in group.members:
                group.members.remove(user_id)
                self._record(IdentityEventKind.MEMBER_REMOVED, user_id, group_id)
            return self._scim_group(group_id)

    # --------------------------------------------------------------- helpers

    def _user(self, user_id: str) -> _User:
        try:
            return self._users[user_id]
        except KeyError:
            raise NotFoundError(f"user {user_id} not found") from None

    def _group(self, group_id: str) -> _Group:
        try:
            return self._groups[group_id]
        except KeyError:
            raise NotFoundError(f"group {group_id} not found") from None

    def _scim_user(self, user_id: str) -> ScimUser:
        user = self._users[user_id]
        groups = sorted(gid for gid, g in self._groups.items() if user_id in g.members)
        return ScimUser(
            id=user_id,
            user_name=user_id,
            display_name=user.name,
            location=user.location,
            active=user.active,
            groups=groups,
        )

    def _scim_group(self, group_id: str) -> ScimGroup:
        group = self._groups[group_id]
        return ScimGroup(id=group_id, display_name=group.description, members=sorted(group.members))

    def _record(self, kind: IdentityEventKind, user_id: str, group: str | None = None) -> None:
        at = self._clock()
        self._feed.append(
            lambda seq: IdentityEvent(seq=seq, event=kind, user_id=user_id, group=group, at=at)
        )
