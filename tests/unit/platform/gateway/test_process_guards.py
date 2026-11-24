"""A second process must see the first worker's deletion and guard decisions."""

from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from multiprocessing.synchronize import Event
from pathlib import Path

import pytest

from ekb.app.gateway import build_gateway
from ekb.platform.gateway.config import GatewayConfig, OptionChoice, Quota
from ekb.platform.gateway.models import ChatRequest, ModelReply
from ekb.platform.gateway.options.fake import FakeClient
from tests.unit.platform.gateway.helpers import ask, make_config


def _config(folder: Path, cache: str) -> GatewayConfig:
    return make_config(
        cache=OptionChoice(option="sqlite", params={"path": str(folder / cache)}),
        cost_log=OptionChoice(option="jsonl", params={"path": str(folder / "log.jsonl")}),
        quotas={"one_q": Quota(per_day=1), "two_q": Quota(per_day=100)},
        max_spend_usd=1e-6,
    )


def _worker(folder: Path, task: str, entered: Event, release: Event) -> None:
    class SlowClient(FakeClient):
        def invoke(self, request: ChatRequest) -> ModelReply:
            entered.set()
            assert release.wait(30)
            return super().invoke(request)

    config = _config(folder, "worker.sqlite")
    clients = {ref: SlowClient(ref.split(":")[1]) for ref in config.models}
    gw = build_gateway(config, clients=clients, environ={})
    ask(gw, "first", task=task, document_key="A")


@pytest.mark.parametrize("operation", ["forget", "quota", "spend"])
def test_overlapping_workers_share_guards(tmp_path: Path, operation: str) -> None:
    context = get_context("spawn")
    entered, release = context.Event(), context.Event()
    task = "paid" if operation == "spend" else "header"
    process = context.Process(target=_worker, args=(tmp_path, task, entered, release))
    cache = "worker.sqlite" if operation == "forget" else "other.sqlite"
    gateway = build_gateway(_config(tmp_path, cache), environ={})
    process.start()
    try:
        assert entered.wait(45)
        with ThreadPoolExecutor() as pool:
            if operation == "forget":
                purge = pool.submit(gateway.forget, "A")
                try:
                    with pytest.raises(TimeoutError):
                        purge.result(timeout=0.25)
                finally:
                    release.set()
                assert purge.result(timeout=30) == 1
                assert gateway.cache.size() == 0
            else:
                reply = pool.submit(ask, gateway, "second", task=task)
                try:
                    with pytest.raises(TimeoutError):
                        reply.result(timeout=0.25)
                finally:
                    release.set()
                assert reply.result(timeout=30).candidate == "fake:two"
    finally:
        release.set()
        process.join(30)
    assert process.exitcode == 0
