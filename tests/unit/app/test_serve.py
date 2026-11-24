"""Serving the product's own apps inside a demo: start, reach, stop early."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from ekb.app.demos._serve import serving


def _app() -> FastAPI:
    app = FastAPI(title="probe")

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"pong": "yes"}

    return app


def test_serving_starts_and_stops_early() -> None:
    with serving(_app()) as served:
        assert served.running
        assert httpx.get(f"{served.url}/ping").json() == {"pong": "yes"}
        served.stop()
        assert not served.running
        with pytest.raises(httpx.TransportError):
            httpx.get(f"{served.url}/ping", timeout=1.0)
