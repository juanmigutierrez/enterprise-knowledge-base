"""HTTP API of the mock docs service (FastAPI).

This is the only way the product reaches the mock, the same way a connector
reaches SharePoint or Confluence over their REST APIs. Run it with
``python -m sources.mock_docs``.

Endpoints (plus ``GET /health`` from the shared factory):

========  ======================  =========================================
GET       /pages                  full listing, no bodies (finds deletes)
GET       /pages/{id}             one page with its body
POST      /pages                  create
PUT       /pages/{id}             save content (new ``rev``)
PUT       /pages/{id}/acl         replace permissions (new ``acl_rev``)
DELETE    /pages/{id}             delete
GET       /changes?since=<seq>    change feed after a cursor
POST      /reset                  reload the synthetic documents
========  ======================  =========================================
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Query, status

from sources.common.directory import UnknownPrincipalError
from sources.common.factory import create_service_app
from sources.mock_docs.models import (
    ChangeFeed,
    Page,
    PageAcl,
    PageCreate,
    PageEdit,
    PageSummary,
    SourceChange,
)
from sources.mock_docs.store import DEFAULT_DATA_DIR, DocsStore, PageExistsError, PageNotFoundError

DEFAULT_PORT = 8081


def create_app(data_dir: Path = DEFAULT_DATA_DIR) -> FastAPI:
    """Build the app with a fresh store loaded from ``data_dir``."""
    app = create_service_app(
        "Mock docs service",
        errors={
            PageNotFoundError: status.HTTP_404_NOT_FOUND,
            PageExistsError: status.HTTP_409_CONFLICT,
            UnknownPrincipalError: status.HTTP_422_UNPROCESSABLE_CONTENT,
        },
    )
    app.state.store = DocsStore.from_seed(data_dir)

    def store() -> DocsStore:
        result: DocsStore = app.state.store
        return result

    @app.get("/pages")
    def list_pages() -> list[PageSummary]:
        return store().list_pages()

    @app.get("/pages/{page_id}")
    def get_page(page_id: str) -> Page:
        return store().get(page_id)

    @app.post("/pages", status_code=status.HTTP_201_CREATED)
    def create_page(request: PageCreate) -> Page:
        return store().create(request)

    @app.put("/pages/{page_id}")
    def edit_page(page_id: str, request: PageEdit) -> Page:
        return store().edit(page_id, request)

    @app.put("/pages/{page_id}/acl")
    def set_acl(page_id: str, acl: PageAcl) -> Page:
        return store().set_acl(page_id, acl)

    @app.delete("/pages/{page_id}")
    def delete_page(page_id: str) -> SourceChange:
        return store().delete(page_id)

    @app.get("/changes")
    def changes(since: Annotated[int, Query(ge=0)] = 0) -> ChangeFeed:
        entries, cursor = store().read_feed(since)
        return ChangeFeed(changes=entries, cursor=cursor)

    @app.post("/reset")
    def reset() -> dict[str, int]:
        app.state.store = DocsStore.from_seed(data_dir)
        return {"pages": len(store().list_pages())}

    return app
