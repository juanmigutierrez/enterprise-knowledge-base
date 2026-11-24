"""A small gateway world: two free fake models with their own quotas, one paid, a fake clock.

Used by the gateway tests and the app tests:
``from tests.unit.platform.gateway.helpers import ...``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ekb.platform.gateway.config import GatewayConfig, ModelSpec, Price, ProviderSpec, Quota
from ekb.platform.gateway.gateway import ModelGateway
from ekb.platform.gateway.models import GatewayReply
from ekb.platform.gateway.options.cache_memory import InMemoryCache
from ekb.platform.gateway.options.fake import FakeClient
from ekb.platform.gateway.options.log_memory import InMemoryCostLog

START = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


class FakeClock:
    """Time that moves only when told to; ``sleep`` moves it and remembers how far."""

    def __init__(self, now: datetime = START) -> None:
        self.now = now
        self.slept: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.advance(seconds)


def make_config(**overrides: object) -> GatewayConfig:
    base: dict[str, object] = {
        "providers": {"fake": ProviderSpec()},
        "quotas": {
            "one_q": Quota(per_minute=10, per_day=3),
            "two_q": Quota(per_minute=10, per_day=100),
        },
        "models": {
            "fake:one": ModelSpec(quota="one_q"),
            "fake:two": ModelSpec(quota="two_q"),
            "fake:paid": ModelSpec(price=Price(input=1.0, output=2.0)),
        },
        "tasks": {
            "header": ("fake:one", "fake:two"),
            "answer": ("fake:two",),
            "paid": ("fake:paid", "fake:two"),
        },
        "max_wait_s": 20,
        "cooldown_s": 600,
    }
    base.update(overrides)
    return GatewayConfig.model_validate(base)


@dataclass
class World:
    gateway: ModelGateway
    clients: dict[str, FakeClient]
    cache: InMemoryCache
    log: InMemoryCostLog
    clock: FakeClock

    def build(self, config: GatewayConfig, max_spend_usd: float | None = None) -> ModelGateway:
        """Another gateway over the same clients, cache, log and clock."""
        return ModelGateway(
            config,
            self.clients,
            self.cache,
            self.log,
            max_spend_usd=max_spend_usd,
            clock=self.clock,
            sleep=self.clock.sleep,
        )


def make_world(config: GatewayConfig | None = None, *, max_spend_usd: float | None = None) -> World:
    config = config or make_config()
    clients = {ref: FakeClient(ref.partition(":")[2]) for ref in config.models}
    cache, log, clock = InMemoryCache(), InMemoryCostLog(), FakeClock()
    gateway = ModelGateway(
        config, clients, cache, log, max_spend_usd=max_spend_usd, clock=clock, sleep=clock.sleep
    )
    return World(gateway, clients, cache, log, clock)


def ask(
    gateway: ModelGateway,
    prompt: str = "Where is the leave policy?",
    *,
    task: str = "header",
    prompt_version: str = "test@1",
    max_output_tokens: int = 50,
    **kwargs: Any,
) -> GatewayReply:
    """A call with sensible defaults; any argument can be overridden."""
    return gateway.complete(
        task, prompt, prompt_version=prompt_version, max_output_tokens=max_output_tokens, **kwargs
    )
