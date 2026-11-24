import pytest
from typer.testing import CliRunner

from ekb.app.cli import app

runner = CliRunner()


def test_options_lists_configured_steps() -> None:
    result = runner.invoke(app, ["options"])
    assert result.exit_code == 0
    assert "t7_embed" in result.output
    assert "fake" in result.output


def test_options_reports_the_effective_spend_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EKB_MAX_SPEND_USD", "1.25")
    result = runner.invoke(app, ["options"])
    assert result.exit_code == 0
    assert "max_spend_usd=1.25" in result.output


def test_unimplemented_service_exits_with_clear_message() -> None:
    result = runner.invoke(app, ["worker"])
    assert result.exit_code == 2
    assert "not implemented yet" in result.output
