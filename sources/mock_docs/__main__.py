"""Run the mock docs service: ``python -m sources.mock_docs [--port 8081]``."""

from __future__ import annotations

from sources.common.factory import serve
from sources.mock_docs.app import DEFAULT_PORT, create_app

if __name__ == "__main__":
    serve(
        create_app,
        prog="python -m sources.mock_docs",
        description=__doc__,
        default_port=DEFAULT_PORT,
    )
