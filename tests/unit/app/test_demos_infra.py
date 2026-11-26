"""The S17 demos over the faked infrastructure: ``ekb demo s3 --docker`` and
``ekb demo infra`` (the S16 story in memory and on the real options, side by side)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sources.mock_docs.app import create_app as docs_app
from sources.mock_hr_api.app import create_app as hr_api_app
from sources.mock_idp.app import create_app as idp_app

from ekb.app.choices import choose
from ekb.app.demos._mocks import fail
from ekb.app.demos.infra import run_infra_demo
from ekb.app.demos.s3 import run_s3_live, run_s3_replay
from ekb.app.pipeline import build_changelog
from ekb.platform.config import load_settings
from ekb.platform.infra import InfrastructureError
from tests.conftest import REPO_ROOT
from tests.fakes.infra import fake_infrastructure
from tests.http import as_httpx

ANSWER_WARNING = "ignore:You should not use the 'timeout' argument with the TestClient"


def test_s3_on_docker_keeps_the_log_where_another_process_finds_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines: list[str] = []
    choices = choose(load_settings(profile="local"), offline=True, docker=True)
    with (
        fake_infrastructure(monkeypatch) as infra,
        TestClient(docs_app()) as docs,
        TestClient(idp_app()) as idp,
    ):
        run_s3_live(as_httpx(docs), as_httpx(idp), lines.append, changelog=build_changelog(choices))
        run_s3_replay(build_changelog(choices), "Stopped.", lines.append, durable=True)
    output = "\n".join(lines)
    assert "bus, 3 partitions (Kafka topic ekb.changes)" in output
    assert "raw store (S3 bucket ekb-raw on SeaweedFS)" in output
    assert "document registry (DynamoDB table ekb-registry)" in output
    assert "p0@8   delete     HR-PL-99  v1.1  by=dana   readers=-" in output  # same partitions
    assert "opened as another process would" in output
    assert "Same as the registry: True." in output
    assert sum(len(p) for p in infra.kafka.topics["ekb.changes"]) > 12


@pytest.mark.filterwarnings(ANSWER_WARNING)
def test_the_same_story_keeps_every_promise_in_memory_and_on_the_real_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(REPO_ROOT)  # the demo starts the HR database's command from here
    monkeypatch.setenv("EKB_PROFILE", "local")
    lines: list[str] = []
    with (
        fake_infrastructure(monkeypatch),
        TestClient(docs_app()) as docs,
        TestClient(hr_api_app()) as hr,
        TestClient(idp_app()) as idp,
    ):
        held = run_infra_demo(
            as_httpx(docs), as_httpx(hr), as_httpx(idp), namespace=None, echo=lines.append
        )
    output = "\n".join(lines)
    assert held, output
    assert "NOT HELD" not in output
    assert "in memory: leaked 0 times, cited a deleted page 0 times" in output
    assert "on Docker: leaked 0 times, cited a deleted page 0 times" in output
    assert "bus kafka" in output
    assert "search opensearch" in output
    assert "Kafka ekb.changes:" in output


def test_a_demo_that_cannot_reach_the_infrastructure_says_how_to_start_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_ = fail(InfrastructureError("kafka: look for topic ekb.changes: timed out"))
    assert exit_.exit_code == 1
    assert "make up" in capsys.readouterr().err
    fail(ValueError("something else"))
    assert "make up" not in capsys.readouterr().err
