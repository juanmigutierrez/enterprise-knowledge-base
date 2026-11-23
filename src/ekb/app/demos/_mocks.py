"""Reach the mock sources from a demo without importing them.

The mocks live outside the product (``sources/``) and ``ekb`` never imports
them (an import-linter contract fails the build if it does). A demo starts a
mock as a separate local process and talks to it the way a connector talks to a
real system: over HTTP for services, with SQL for the database.
"""

from __future__ import annotations

import socket
import subprocess  # nosec B404: starts the local mocks with fixed commands
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import typer

Echo = Callable[[str], None]

STARTUP_TIMEOUT_S = 20.0
COMMAND_TIMEOUT_S = 60.0


class MockStartError(RuntimeError):
    """A mock could not be started, did not become healthy, or its command failed."""


def _module_dir(module: str, root: Path) -> Path:
    path = root / Path(*module.split("."))
    if not path.is_dir():
        raise MockStartError(
            f"{module} not found under {root}. Run this from the repository root, "
            "or start the mock yourself and pass --url."
        )
    return path


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def _wait_healthy(url: str, process: subprocess.Popen[bytes], timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise MockStartError(f"the mock exited early with code {process.returncode}")
        try:
            if httpx.get(f"{url}/health", timeout=1.0).status_code == httpx.codes.OK:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    raise MockStartError(f"the mock did not answer on {url} within {timeout_s:.0f}s")


@contextmanager
def running_mock(
    module: str, *, repo_root: Path | None = None, timeout_s: float = STARTUP_TIMEOUT_S
) -> Iterator[str]:
    """Start ``python -m <module> --port <free port>``; yield its base URL; stop it.

    The mocks are plain folders in the repository, not installed packages, so
    the process runs from the repository root.
    """
    root = repo_root or Path.cwd()
    _module_dir(module, root)
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    command = [sys.executable, "-m", module, "--port", str(port)]
    process = subprocess.Popen(command, cwd=root)  # noqa: S603  # nosec B603: fixed command, no shell
    try:
        _wait_healthy(url, process, timeout_s)
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


@contextmanager
def mock_client(module: str, url: str | None = None) -> Iterator[httpx.Client]:
    """A client for the mock: the one at ``url`` if given, else a freshly started one."""
    if url:
        with httpx.Client(base_url=url, timeout=10.0) as client:
            yield client
        return
    with running_mock(module) as started, httpx.Client(base_url=started, timeout=10.0) as client:
        yield client


def run_mock_command(module: str, args: Sequence[str], *, repo_root: Path | None = None) -> str:
    """Run ``python -m <module> <args>`` to completion and return its output."""
    root = repo_root or Path.cwd()
    _module_dir(module, root)
    command = [sys.executable, "-m", module, *args]
    result = subprocess.run(  # noqa: S603  # nosec B603: fixed command, no shell
        command, cwd=root, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S, check=False
    )
    if result.returncode != 0:
        raise MockStartError(f"{module} {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def call(client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
    """One HTTP call; an error status raises, a body is returned as JSON (or None)."""
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else None


def fail(exc: Exception) -> typer.Exit:
    """Print a failed demo's reason and return the exit to raise."""
    typer.secho(f"Demo failed: {exc}", fg="red", err=True)
    return typer.Exit(code=1)
