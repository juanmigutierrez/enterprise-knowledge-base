"""The mock docs service's own data shapes.

They imitate what a docs tool exposes: pages with a revision number, a
permission list and a change feed. They are deliberately not the product's
``ChangeEvent``; the gap between the two is what the connector (T1) closes.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PageAcl(BaseModel):
    """Who may read a page: user and group names from the directory.

    Stored sorted and without duplicates so the same permissions always look
    the same, whatever order an editor typed them in.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    users: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()

    @field_validator("users", "groups")
    @classmethod
    def _sorted_unique(cls, names: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(names)))

    @property
    def is_empty(self) -> bool:
        return not self.users and not self.groups


class PageSummary(BaseModel):
    """A page as it appears in a listing: everything except the body.

    A full listing is how a connector finds deletes in sources without
    webhooks, so it must be cheap: it carries versions and hashes, not text.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str
    language: str = Field(pattern=r"^[a-z]{2}$")
    department: str
    acl: PageAcl
    rev: int = Field(ge=1)
    acl_rev: int = Field(ge=1)
    content_hash: str
    updated_at: datetime
    modified_by: str | None = None  # who made the last change; None for seeded pages


class Page(PageSummary):
    """A page with its body."""

    body: str
    first_rev: int = Field(ge=1)


class PageCreate(BaseModel):
    """Request body to create a page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[A-Z0-9-]+$")
    title: str = Field(min_length=1)
    language: str = Field(pattern=r"^[a-z]{2}$")
    department: str
    body: str
    acl: PageAcl


class PageEdit(BaseModel):
    """Request body to save a page. A missing title keeps the current one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    body: str
    title: str | None = Field(default=None, min_length=1)


class ChangeKind(StrEnum):
    """The change types the mock reports, named the way a docs tool names them."""

    CREATED = "page.created"
    UPDATED = "page.updated"
    PERMISSIONS_CHANGED = "page.permissions_changed"
    DELETED = "page.deleted"


class SourceChange(BaseModel):
    """One entry in the change feed: what a webhook would carry.

    It says *that* something changed, not *what* the new content is. A
    connector reacting to it fetches the page, as with real webhooks.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int = Field(ge=1, description="Position in the feed; use it as the cursor.")
    event: ChangeKind
    page_id: str
    rev: int
    acl_rev: int
    at: datetime
    actor: str | None = None  # who made the change (the X-Actor header), if anyone said


class ChangeFeed(BaseModel):
    """A page of the change feed plus the cursor to ask for next time."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    changes: list[SourceChange]
    cursor: int
