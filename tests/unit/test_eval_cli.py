from pathlib import Path

from typer.testing import CliRunner

from ekb.evaluation.cli import app

runner = CliRunner()


def test_missing_experiment_file_is_an_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 1


def test_runner_not_built_yet_says_so(tmp_path: Path) -> None:
    experiment = tmp_path / "x.yaml"
    experiment.write_text("card: '4.4'\n")
    result = runner.invoke(app, ["run", str(experiment)])
    assert result.exit_code == 2
    assert "not implemented yet" in result.output
