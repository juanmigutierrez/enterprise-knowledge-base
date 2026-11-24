"""The webhook receiver: the HTTP endpoint a source POSTs notifications to.

It is deliberately thin: check the body, hand it to the connector's queue,
answer ``202 Accepted``. Fetching and normalizing happen later in the
connector's ``sync``, so the source gets its answer fast.

It takes a callable rather than a connector class, so it does not import a
concrete option (only ``ekb.app`` wires options).
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, HTTPException, status

from ekb.connect.docs_api import DocsNotification
from ekb.connect.normalize.common import UnknownChangeKindError

DOCS_WEBHOOK_PATH = "/webhooks/docs"


def create_receiver_app(on_docs_notification: Callable[[DocsNotification], None]) -> FastAPI:
    app = FastAPI(title="ekb webhook receiver", version="1")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(DOCS_WEBHOOK_PATH, status_code=status.HTTP_202_ACCEPTED)
    def docs_webhook(notification: DocsNotification) -> dict[str, str]:
        try:
            on_docs_notification(notification)
        except UnknownChangeKindError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        return {"status": "queued"}

    return app
