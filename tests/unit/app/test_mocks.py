"""How demos reach the mocks: separate processes, never imports."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from ekb.app.demos import _mocks
from tests.conftest import REPO_ROOT


def _fake_module(root: Path, name: str, main: str) -> None:
    folder = root / name
    folder.mkdir()
    (folder / "__init__.py").write_text("")
    (folder / "__main__.py").write_text(main)


def test_running_mock_starts_a_real_process_and_stops_it() -> None:
    with _mocks.running_mock("sources.mock_docs", repo_root=REPO_ROOT) as url:
        assert httpx.get(f"{url}/health").json() == {"status": "ok"}
    with pytest.raises(httpx.TransportError):
        httpx.get(f"{url}/health", timeout=1.0)


def test_running_mock_explains_where_to_run_from(tmp_path: Path) -> None:
    with (
        pytest.raises(_mocks.MockStartError, match="repository root"),
        _mocks.running_mock("sources.mock_docs", repo_root=tmp_path),
    ):
        pass


def test_running_mock_reports_a_process_that_dies(tmp_path: Path) -> None:
    _fake_module(tmp_path, "broken_mock", "raise SystemExit(3)\n")
    with (
        pytest.raises(_mocks.MockStartError, match="exited early with code 3"),
        _mocks.running_mock("broken_mock", repo_root=tmp_path),
    ):
        pass


def test_running_mock_times_out(tmp_path: Path) -> None:
    _fake_module(tmp_path, "slow_mock", "import time\ntime.sleep(30)\n")
    with (
        pytest.raises(_mocks.MockStartError, match="did not answer"),
        _mocks.running_mock("slow_mock", repo_root=tmp_path, timeout_s=0.5),
    ):
        pass


def test_run_mock_command_returns_output_or_raises(tmp_path: Path) -> None:
    _fake_module(tmp_path, "echo_mock", "import sys\nprint('hello', *sys.argv[1:])\n")
    assert _mocks.run_mock_command("echo_mock", ["a"], repo_root=tmp_path).strip() == "hello a"
    _fake_module(tmp_path, "failing_mock", "import sys\nsys.exit('bad input')\n")
    with pytest.raises(_mocks.MockStartError, match="bad input"):
        _mocks.run_mock_command("failing_mock", [], repo_root=tmp_path)
