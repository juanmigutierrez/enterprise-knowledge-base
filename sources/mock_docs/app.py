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
GET       /subscriptions          webhook receivers and their delivery counts
POST      /subscriptions          subscribe a receiver URL (webhook push, S2)
DELETE    /subscriptions/{id}     unsubscribe
POST      /reset                  reload the synthetic documents
========  ======================  =========================================

Writes may name who makes them with an ``X-Actor: <user id>`` header (an
identity provider user, checked against the directory); it is kept as the
page's ``modified_by`` and the feed entry's ``actor``.

Subscriptions survive ``/reset``: a reset reloads the documents, not the
receivers listening to them. Seeding sends no webhooks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, Query, status

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
from sources.mock_docs.webhooks import (
    Post,
    Subscription,
    SubscriptionCreate,
    UnknownSubscriptionError,
    WebhookPublisher,
    http_post,
)

DEFAULT_PORT = 8081

Actor = Annotated[str | None, Header(alias="X-Actor")]


def create_app(data_dir: Path = DEFAULT_DATA_DIR, *, post: Post = http_post) -> FastAPI:
    """Build the app with a fresh store loaded from ``data_dir``.

    ``post`` delivers webhooks; tests replace it to capture them.
    """
    app = create_service_app(
        "Mock docs service",
        errors={
            PageNotFoundError: status.HTTP_404_NOT_FOUND,
            PageExistsError: status.HTTP_409_CONFLICT,
            UnknownPrincipalError: status.HTTP_422_UNPROCESSABLE_CONTENT,
            UnknownSubscriptionError: status.HTTP_404_NOT_FOUND,
        },
    )
    publisher = WebhookPublisher(post)

    def fresh_store() -> DocsStore:
        new = DocsStore.from_seed(data_dir)  # seeded first, so seeding sends no webhooks
        new.add_listener(publisher.publish)
        return new

    app.state.store = fresh_store()

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
    def create_page(request: PageCreate, actor: Actor = None) -> Page:
        return store().create(request, actor=actor)

    @app.put("/pages/{page_id}")
    def edit_page(page_id: str, request: PageEdit, actor: Actor = None) -> Page:
        return store().edit(page_id, request, actor=actor)

    @app.put("/pages/{page_id}/acl")
    def set_acl(page_id: str, acl: PageAcl, actor: Actor = None) -> Page:
        return store().set_acl(page_id, acl, actor=actor)

    @app.delete("/pages/{page_id}")
    def delete_page(page_id: str, actor: Actor = None) -> SourceChange:
        return store().delete(page_id, actor=actor)

    @app.get("/changes")
    def changes(since: Annotated[int, Query(ge=0)] = 0) -> ChangeFeed:
        entries, cursor = store().read_feed(since)
        return ChangeFeed(changes=entries, cursor=cursor)

    @app.get("/subscriptions")
    def list_subscriptions() -> list[Subscription]:
        return publisher.subscriptions()

    @app.post("/subscriptions", status_code=status.HTTP_201_CREATED)
    def subscribe(request: SubscriptionCreate) -> Subscription:
        return publisher.subscribe(request.url)

    @app.delete("/subscriptions/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
    def unsubscribe(subscription_id: int) -> None:
        publisher.unsubscribe(subscription_id)

    @app.post("/reset")
    def reset() -> dict[str, int]:
        app.state.store = fresh_store()
        return {"pages": len(store().list_pages())}

    return app
