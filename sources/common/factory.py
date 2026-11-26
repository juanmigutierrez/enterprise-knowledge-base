"""Object factory for the mock services: build an app, run it from the command line.

Every HTTP mock needs the same frame: a ``/health`` endpoint (``ekb demo`` waits
on it), its domain errors turned into HTTP status codes, and a ``__main__`` that
serves it on 127.0.0.1. Building that frame here keeps each mock's ``app.py``
down to its own endpoints.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ErrorMap = Mapping[type[Exception], int]


def create_service_app(title: str, *, errors: ErrorMap) -> FastAPI:
    """A FastAPI app with ``/health`` and ``errors`` mapped to status codes.

    The error message becomes the response ``detail``, so a client sees why a
    request was refused (unknown group, page not found ...).
    """
    app = FastAPI(title=title, version="1")

    async def _handle(_: Request, exc: Exception) -> JSONResponse:
        status_code = next(code for kind, code in errors.items() if isinstance(exc, kind))
        detail = exc.args[0] if exc.args else type(exc).__name__
        return JSONResponse({"detail": str(detail)}, status_code=status_code)

    for kind in errors:
        app.add_exception_handler(kind, _handle)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def serve(
    app_factory: Callable[[], FastAPI],
    *,
    prog: str,
    description: str | None,
    default_port: int,
    argv: list[str] | None = None,
) -> None:
    """Parse ``--port`` (and ``--host``) and serve the app, on 127.0.0.1 unless told
    otherwise: a local test double. Inside a container (S17) it listens on 0.0.0.0, and
    ``compose.yaml`` publishes the port on the host's 127.0.0.1 only."""
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument("--port", type=int, default=default_port)
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on")
    args = parser.parse_args(argv)
    uvicorn.run(app_factory(), host=args.host, port=args.port, log_level="warning")
