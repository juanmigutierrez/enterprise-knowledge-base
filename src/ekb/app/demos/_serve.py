"""Serve one of the product's own HTTP apps inside the demo process.

The webhook receiver must be reachable by the mock (a separate process), so
the demo runs it on a free local port in a background thread, and can stop it
mid-story to show what happens when a receiver is down.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn
from starlette.applications import Starlette

from ekb.app.demos._mocks import STARTUP_TIMEOUT_S, MockStartError, free_port


class ServedApp:
    """A running app: its base URL, and a way to stop it early."""

    def __init__(self, server: uvicorn.Server, thread: threading.Thread, url: str) -> None:
        self._server = server
        self._thread = thread
        self.url = url

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


@contextmanager
def serving(
    app: Starlette,
    *,
    timeout_s: float = STARTUP_TIMEOUT_S,
    host: str = "127.0.0.1",
    port: int | None = None,
) -> Iterator[ServedApp]:
    """Serve ``app`` in a background thread: on 127.0.0.1 and a free port unless told
    otherwise (``ekb connect`` in a container listens on 0.0.0.0, port 8090, S17)."""
    port = port or free_port()
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name=f"serve-{port}", daemon=True)
    thread.start()
    served = ServedApp(server, thread, f"http://127.0.0.1:{port}")
    try:
        deadline = time.monotonic() + timeout_s
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise MockStartError(
                    f"could not serve {getattr(app, 'title', 'the app')} on port {port}"
                )
            time.sleep(0.05)
        yield served
    finally:
        served.stop()
