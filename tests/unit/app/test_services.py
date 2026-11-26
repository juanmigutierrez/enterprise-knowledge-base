"""The three services (S17): ekb connect, ekb worker and ekb api --query-only, each with
its own change log and stores, meeting only in the shared infrastructure.

Here the infrastructure is faked (``tests.fakes.infra``): every service builds its
own Kafka bus, DynamoDB tables, S3 store and OpenSearch store, as a separate
process would, and they share only what the fakes hold, as they would share
Kafka, DynamoDB, S3 and OpenSearch. Integration-marked contracts and gates in
``tests/contract/`` and ``tests/gates/`` exercise the stores against Docker.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sources.mock_docs.app import create_app as docs_app
from sources.mock_hr_api.app import create_app as hr_api_app
from sources.mock_hr_db.db import create_database
from sources.mock_idp.app import create_app as idp_app
from typer.testing import CliRunner

from ekb.access.auth.options.static_tokens import StaticTokens
from ekb.app.choices import choose, choose_assistant, choose_query
from ekb.app.cli import app
from ekb.app.infra import SharedInfrastructureError, require_shared
from ekb.app.lifecycle import OLD_TRAVEL, OLD_TRAVEL_KEY, Sources
from ekb.app.services import ANSWER_GROUP, ConnectService, QueryService, WorkerService
from ekb.platform.config import load_settings
from tests.fakes.infra import FakeInfrastructure, fake_infrastructure
from tests.gates.story import caller, pages
from tests.http import as_httpx

HOTEL = "What is the hotel limit per night?"
pytestmark = pytest.mark.filterwarnings(
    "ignore:You should not use the 'timeout' argument with the TestClient"
)


@dataclass
class Deployment:
    connect: ConnectService
    worker: WorkerService
    api: QueryService
    sources: Sources
    infra: FakeInfrastructure

    def settle(self, *, listings: bool = True) -> None:
        """One round of each service, in the order a change flows through them."""
        self.connect.round(listings=listings)
        self.worker.round(listings=listings)
        self.api.round()


@pytest.fixture
def deployment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Deployment]:
    create_database(tmp_path / "hr.sqlite")
    monkeypatch.setenv("EKB_PROFILE", "local")
    settings = load_settings()
    write = choose(settings, offline=True, docker=True)
    with (
        fake_infrastructure(monkeypatch) as infra,
        TestClient(docs_app(post=lambda url, payload: None)) as docs,
        TestClient(hr_api_app()) as hr,
        TestClient(idp_app()) as idp,
    ):
        connect = ConnectService(
            as_httpx(docs), as_httpx(hr), as_httpx(idp), tmp_path / "hr.sqlite", write
        )
        worker = WorkerService(write)
        api = QueryService(
            as_httpx(docs),
            as_httpx(idp),
            write=write,
            query=choose_query(settings),
            assistant=choose_assistant(settings, offline=True),
            tokens=StaticTokens(issued=()),
        )
        yield Deployment(
            connect, worker, api, Sources(as_httpx(docs), as_httpx(hr), as_httpx(idp)), infra
        )


def test_a_change_flows_from_the_connector_through_the_worker_to_the_api(
    deployment: Deployment,
) -> None:
    assert deployment.api.stores.search.chunks() == ()  # nothing yet: three empty services
    deployment.settle()
    assert deployment.api.stores.search.documents()  # the worker filled what the API reads
    reply = deployment.api.assistant.answer(caller("alice"), HOTEL).reply
    assert OLD_TRAVEL_KEY in pages(reply)
    assert deployment.infra.kafka.topics["ekb.changes"]  # one topic, shared


def test_a_delete_hides_the_page_from_the_api_before_the_worker_purges_it(
    deployment: Deployment,
) -> None:
    """The deletion gate across processes: the connector's accepted delete closes the
    page's lifetime in the shared acceptance log, so the API stops returning it at
    once, and the API's own group empties its cached answers."""
    deployment.settle()
    first = deployment.api.assistant.answer(caller("alice"), HOTEL)
    assert OLD_TRAVEL_KEY in pages(first.reply)
    deployment.sources.docs.request(
        "DELETE", f"/pages/{OLD_TRAVEL}", headers={"X-Actor": "dana"}
    ).raise_for_status()
    deployment.connect.round(listings=True)  # accepted, not processed: the worker waits
    deployment.api.round()  # the API's own group reads the delete
    assert deployment.api.forgotten_answers == 1  # alice's cached answer cited the page
    assert deployment.api.stores.search.chunks(OLD_TRAVEL_KEY) == ()
    second = deployment.api.assistant.answer(caller("alice"), HOTEL)
    assert OLD_TRAVEL_KEY not in pages(second.reply)
    deployment.worker.round(listings=True)
    assert deployment.worker.stores.search.chunks_for_update(OLD_TRAVEL_KEY) == ()
    lags = {g.group: g.lag for g in deployment.api.changelog.bus.stats().groups}
    assert (lags["worker"], lags[ANSWER_GROUP]) == (0, 0)


def test_a_service_refuses_to_run_with_its_change_log_or_stores_in_memory() -> None:
    write = choose(load_settings(profile="ci"), offline=True, docker=True)
    with pytest.raises(SharedInfrastructureError, match="would be in memory"):
        require_shared(write, "ekb worker")
    runner = CliRunner()
    for command in (["worker"], ["connect"], ["api", "--query-only"]):
        result = runner.invoke(app, command)
        assert result.exit_code == 2, command
        assert "would be in memory" in result.output
