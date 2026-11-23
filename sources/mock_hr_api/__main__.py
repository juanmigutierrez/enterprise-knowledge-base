"""Run the mock HR record API: ``python -m sources.mock_hr_api [--port 8082]``."""

from __future__ import annotations

from sources.common.factory import serve
from sources.mock_hr_api.app import DEFAULT_PORT, create_app

if __name__ == "__main__":
    serve(
        create_app,
        prog="python -m sources.mock_hr_api",
        description=__doc__,
        default_port=DEFAULT_PORT,
    )
