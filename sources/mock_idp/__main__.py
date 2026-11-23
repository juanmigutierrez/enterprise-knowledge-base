"""Run the mock identity provider: ``python -m sources.mock_idp [--port 8083]``."""

from __future__ import annotations

from sources.common.factory import serve
from sources.mock_idp.app import DEFAULT_PORT, create_app

if __name__ == "__main__":
    serve(
        create_app,
        prog="python -m sources.mock_idp",
        description=__doc__,
        default_port=DEFAULT_PORT,
    )
