"""Shapes of the mock identity provider: a simplified subset of SCIM 2.0.

SCIM is the standard identity providers use to publish users and groups. We keep
its vocabulary (``userName``, ``displayName``, ``active``, ``members``, list
responses with ``totalResults`` and ``Resources``) but not the full protocol:
no filters, no PATCH operations, no schemas URNs.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ScimUser(BaseModel):
    """A user. ``displayName`` is the full name: the 1-1 link to the HR record."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    id: str
    user_name: str = Field(alias="userName")
    display_name: str = Field(alias="displayName")
    location: str
    active: bool
    groups: list[str]


class ScimGroup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    id: str
    display_name: str = Field(alias="displayName")
    members: list[str]  # user IDs, active or not


class ListResponse[R](BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    total_results: int = Field(alias="totalResults")
    resources: list[R] = Field(alias="Resources")


class UserCreate(BaseModel):
    """A joiner: the user and the groups they start in."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9.-]*$")
    name: str = Field(min_length=1)
    location: str
    groups: list[str] = Field(default_factory=list)


class MemberAdd(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str


class IdentityEventKind(StrEnum):
    USER_CREATED = "user.created"
    USER_DEACTIVATED = "user.deactivated"
    MEMBER_ADDED = "group.member_added"
    MEMBER_REMOVED = "group.member_removed"


class IdentityEvent(BaseModel):
    """One entry in the identity event feed (what an Okta event hook would carry)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int = Field(ge=1)
    event: IdentityEventKind
    user_id: str
    group: str | None = None  # set for membership events
    at: datetime


class IdentityEventFeed(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    events: list[IdentityEvent]
    cursor: int
