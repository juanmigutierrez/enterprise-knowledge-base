"""A Starlette ``TestClient`` handed to product code that expects an ``httpx.Client``.

Since S7, LangChain's dependencies (google-genai, langsmith, huggingface-hub)
install ``httpx2``, and Starlette's ``TestClient`` prefers it when present: a
``TestClient`` is now an ``httpx2.Client``. It has the same API, so the product
code talks to the mocks exactly as before; only the type differs, and
:func:`as_httpx` says so in one place.

One real difference to keep in mind when writing a test: errors raised through a
``TestClient`` are ``httpx2`` errors, which ``except httpx.HTTPError`` in product
code does not catch. To test how product code handles an HTTP error, use an
``httpx.Client`` with ``httpx.MockTransport``, not a ``TestClient``.
"""

from __future__ import annotations

from typing import cast

import httpx
from fastapi.testclient import TestClient


def as_httpx(client: TestClient) -> httpx.Client:
    return cast(httpx.Client, client)
