"""T1 for the identity provider: every change becomes an upsert of one user.

Each user is one tracked item, ``users/<id>``, whose history is the list of its
events (decided in S2a): joining, leaving and every group change are upserts of
the user's current record, groups and ``active`` flag included. A leaver is
**not** a delete: the identity provider keeps them listed, inactive.

Working out which pages a group change opens or closes is permission
resolution (T8, S11), not the connector's job: the connector doesn't know
which pages a group can read.

``person_id`` is the user's own ID: the identity provider is the authority on
person IDs. The records are company-directory data, readable by ``all-staff``.
"""

from __future__ import annotations

from datetime import datetime

from ekb.connect.identity_api import IdpUser
from ekb.connect.normalize.common import ALL_STAFF, UnknownChangeKindError, record_upsert
from ekb.contracts import ChangeEvent

KINDS = frozenset(
    {"user.created", "user.deactivated", "group.member_added", "group.member_removed"}
)


def check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise UnknownChangeKindError(f"unknown identity event {kind!r}")


def source_id_of(user_id: str) -> str:
    return f"users/{user_id}"


def user_event(
    source: str,
    user: IdpUser,
    *,
    version: str,
    occurred_at: datetime,
    raw_pointer: str,
    original: bytes,
    changed_by: str | None,
) -> ChangeEvent:
    return record_upsert(
        source,
        source_id_of(user.id),
        version=version,
        occurred_at=occurred_at,
        acl=ALL_STAFF,
        raw_pointer=raw_pointer,
        original=original,
        person_id=user.id,
        changed_by=changed_by,
    )
