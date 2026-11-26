"""``ekb.app.infra`` (S17): a run's namespace, and a clean-up that never hides an error."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
import yaml

from ekb.app import infra
from ekb.platform.aws import EMULATOR_CREDENTIALS
from ekb.platform.infra import ENDPOINTS, InfrastructureError


def test_compose_builds_each_shared_service_image_once() -> None:
    services = yaml.safe_load(Path("compose.yaml").read_text(encoding="utf-8"))["services"]
    builds = Counter(s["image"] for s in services.values() if "build" in s)
    assert builds == {"ekb:local": 1, "ekb-sources:local": 1}


def test_a_run_in_memory_gets_no_namespace_and_drops_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dropped: list[str] = []
    monkeypatch.setattr(infra, "drop", dropped.append)
    with infra.run_namespace(False) as namespace:
        assert namespace is None
    assert dropped == []


def test_a_run_on_docker_gets_a_fresh_namespace_dropped_at_the_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dropped: list[str] = []
    monkeypatch.setattr(infra, "drop", dropped.append)
    with infra.run_namespace(True, "demo") as first, infra.run_namespace(True, "demo") as second:
        assert first is not None
        assert first.startswith("demo-")
        assert first != second
    assert dropped == [second, first]


def test_a_failed_drop_never_hides_the_runs_own_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(namespace: str) -> None:
        raise TimeoutError  # what a Kafka admin call raises when no broker answers

    monkeypatch.setattr(infra, "drop", refuse)
    with pytest.raises(InfrastructureError, match="the real error"), infra.run_namespace(True):
        raise InfrastructureError("the real error")


def test_the_services_namespace_is_never_dropped() -> None:
    with pytest.raises(ValueError, match="refusing to drop the services' namespace"):
        infra.drop("ekb")


def test_local_clients_match_the_compose_endpoints() -> None:
    services = yaml.safe_load(Path("compose.yaml").read_text(encoding="utf-8"))["services"]
    bootstrap = ENDPOINTS["kafka"][1]
    assert bootstrap.startswith("127.0.0.1:")
    assert f"HOST://{bootstrap}" in services["kafka"]["environment"]["KAFKA_ADVERTISED_LISTENERS"]
    assert EMULATOR_CREDENTIALS[0].isascii()
    assert EMULATOR_CREDENTIALS[0].isalnum()
