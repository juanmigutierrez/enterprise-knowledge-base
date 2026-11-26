from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from tests.gates.story import built
from typer.testing import CliRunner

from ekb.app.eval_world import EvalWorld
from ekb.app.gateway import build_gateway
from ekb.evaluation import runner
from ekb.evaluation.cli import app
from ekb.evaluation.quality import evaluate, paired_delta, questions, validate_labels
from ekb.evaluation.scenarios import GateError, permission_quality, permissions, propagation
from ekb.platform.config import load_settings

DATA = Path("evaluation/datasets")


def test_frozen_labels_resolve_through_the_worker(tmp_path: Path) -> None:
    dev = questions(DATA / "golden_v1.jsonl")
    final = questions(DATA / "holdout_v1.jsonl")
    assert (len(dev), len(final)) == (80, 20)
    assert not {q.question for q in dev} & {q.question for q in final}
    assert not {q.id for q in dev} & {q.id for q in final}
    with built(tmp_path, "local") as story:
        validate_labels(story.system, dev + final)
        changed = dev[0].model_copy(update={"allowed_users": ("bob",)})
        with pytest.raises(ValueError, match="reader labels"):
            validate_labels(story.system, [changed])
        ranked = evaluate(story.system, dev[:2], "bm25", 5)
        assert paired_delta(ranked, ranked) == (0, 0)
        with pytest.raises(ValueError, match="identical questions"):
            paired_delta(ranked, ranked[::-1])
        with pytest.raises(ValueError, match="real embeddings"):
            evaluate(story.system, dev[:1], "vector", 5)


@pytest.mark.parametrize("card", ["4.13", "4.14"])
def test_actual_gate_scenarios(card: str, tmp_path: Path) -> None:
    with built(tmp_path, "local") as story:
        world = EvalWorld(story.system, story.sources, story.outage)
        if card == "4.13":
            comparison = permission_quality(world, questions(DATA / "golden_v1.jsonl")[:2])
            assert any("| combined |" in line for line in comparison)
        lines = permissions(world) if card == "4.13" else propagation(world)
        assert any("0/" in line for line in lines)


def test_gate_failure_is_saved_and_disqualifies_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "experiments" / "permission.yaml"
    path.parent.mkdir()
    path.write_text('card: "4.13"\nquestion: test')

    @contextmanager
    def unavailable() -> Iterator[EvalWorld]:
        raise GateError("warm cache leaked")
        yield  # pragma: no cover

    monkeypatch.setattr(runner, "loaded", unavailable)
    report, passed = runner.run_card(path)
    assert not passed
    assert "warm cache leaked" in report.read_text(encoding="utf8")


def test_cli_runs_frozen_development_rows_and_preserves_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiments = tmp_path / "experiments"
    datasets = tmp_path / "datasets"
    experiments.mkdir()
    datasets.mkdir()
    path = experiments / "retrieval.yaml"
    path.write_text(
        'card: "4.5"\nquestion: Test ranking\nmodes: [bm25]\n'
        'options: [{route: "ollama:bge-m3"}, {route: "bedrock:unused"}]\n'
    )
    rows = questions(DATA / "golden_v1.jsonl")
    (datasets / "golden_v1.jsonl").write_text(rows[0].model_dump_json(), encoding="utf8")
    gateway = build_gateway(load_settings(profile="ci").gateway)
    # Supply the offline store fixture. No vector leg is requested in this card.
    monkeypatch.setattr(runner, "experiment_gateway", lambda *_: gateway)

    @contextmanager
    def fixture_world(*_: object, **kwargs: object) -> Iterator[EvalWorld]:
        with built(tmp_path, "local") as story:
            yield EvalWorld(story.system, story.sources, story.outage)

    monkeypatch.setattr(runner, "loaded", fixture_world)
    summary = tmp_path / "summary.md"
    summary.write_text("earlier run\n", encoding="utf8")
    result = CliRunner().invoke(app, ["run", str(path), "--summary", str(summary)])
    assert result.exit_code == 0, result.output
    report = summary.read_text(encoding="utf8")
    assert report.startswith("earlier run\n")
    assert "golden_v1 (1 questions)" in report
    assert "hosted route requires --paid" in report
    assert "Run status: passed" in report
    assert len(list((tmp_path / "results" / "raw").glob("*.jsonl"))) == 1
