"""Reading the identity provider's user records as memberships.

The identity sync connector (S2a) stores each user as the provider sent it
(canonical JSON of the SCIM user) and emits one upsert per change: a joiner, a
leaver, a group added or removed. A **membership reader** turns one stored
record into the :class:`~ekb.contracts.Membership` the permission store keeps:

====================  ============================================
the record             the membership
====================  ============================================
``users/alice``        alice, active, ``{all-staff, it-employees}``
``users/olivia``       olivia, **inactive**, her groups kept (S1c)
====================  ============================================

Like T6's record readers, it keeps only the fields it needs, so a source adding
a field breaks nothing. Which reader goes with which source is chosen in the
wiring (``ekb.app.permissions``).
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, ValidationError

from ekb.contracts import ChangeEvent, Membership

MembershipReader = Callable[[ChangeEvent, bytes], Membership]
"""``(upsert event, stored original) -> Membership``; raises :class:`RecordError`
when the record can't be read."""


class RecordError(ValueError):
    """A stored record is not what its source promised. Not skipped: the event comes back."""


class _User(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    active: bool
    groups: list[str]


def idp_user(event: ChangeEvent, original: bytes) -> Membership:
    """An identity provider user record (``users/<id>``) → their membership."""
    collection, _, user_id = event.source_id.partition("/")
    if collection != "users" or not user_id:
        raise RecordError(f"{event.document_key}: memberships are read from users/<id> records")
    try:
        user = _User.model_validate_json(original)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "the record"
        raise RecordError(
            f"{event.document_key} v{event.version}: not an identity user record "
            f"({where}: {first['msg']})"
        ) from exc
    if user.id != user_id:
        raise RecordError(f"{event.document_key}: the stored record is {user.id}, not {user_id}")
    return Membership(
        user_id=user.id,
        active=user.active,
        groups=frozenset(user.groups),
        source_key=event.document_key,
        source_version=event.version,
        original_lifetime=event.original_lifetime,
    )
