"""Client for the docs tool's REST API, as the connector sees it.

The product never imports the mock (``sources/``); it reads the API the way it
would read SharePoint or Confluence. So the connector keeps its own copy of
the few message formats it needs. The copies ignore fields they don't know
(``extra="ignore"``): a source adding a field must not break the connector.
"""

from __future__ import annotations

from datetime import datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field


class _Wire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class DocsAcl(_Wire):
    users: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()


class DocsPageSummary(_Wire):
    """One entry of ``GET /pages``: versions and hash, no body."""

    id: str
    acl: DocsAcl
    rev: int
    acl_rev: int
    content_hash: str
    updated_at: datetime
    modified_by: str | None = None


class DocsPage(DocsPageSummary):
    """``GET /pages/{id}``. Only what T1 needs; the full original is kept as bytes."""

    title: str
    first_rev: int | None = Field(default=None, ge=1, strict=True)


class DocsNotification(_Wire):
    """A webhook body: *that* a page changed, never its content."""

    seq: int
    event: str  # kept as text: an unknown kind must be reported, not rejected on arrival
    page_id: str
    rev: int
    acl_rev: int
    at: datetime
    actor: str | None = None


class FetchedPage(BaseModel):
    """A page plus the exact bytes the source sent, which become the stored original."""

    model_config = ConfigDict(frozen=True)

    page: DocsPage
    original: bytes


class DocsApi:
    """The calls the connectors and Q4's access check make. Error statuses other than 404 raise."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def list_pages(self) -> list[DocsPageSummary]:
        response = self._client.get("/pages")
        response.raise_for_status()
        return [DocsPageSummary.model_validate(item) for item in response.json()]

    def get_page(self, page_id: str, *, timeout: float | None = None) -> FetchedPage | None:
        """The page now, or ``None`` if the source no longer has it.

        ``timeout`` (seconds) overrides the client's for this call: Q4's access check
        (S14) waits less than a connector syncing in the background.
        """
        path = f"/pages/{page_id}"
        if timeout is None:
            response = self._client.get(path)
        else:
            response = self._client.get(path, timeout=timeout)
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        response.raise_for_status()
        return FetchedPage(
            page=DocsPage.model_validate_json(response.content), original=response.content
        )
