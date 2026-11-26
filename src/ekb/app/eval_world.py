"""Give each experiment fresh sources, stores, and a gateway restricted to its embedding task."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ekb.access.auth.options.static_tokens import StaticTokens
from ekb.app.choices import choose, choose_assistant, choose_query
from ekb.app.demos._mocks import mock_client, run_mock_command
from ekb.app.demos.mcp import Outage
from ekb.app.demos.search import DB, DOCS, HR, IDP
from ekb.app.gateway import MAX_SPEND_ENV, build_gateway, max_spend
from ekb.app.lifecycle import Sources
from ekb.app.system import KnowledgeSystem
from ekb.platform.config import StepChoice, load_settings
from ekb.platform.gateway.config import GatewayConfig, OptionChoice
from ekb.platform.gateway.gateway import ModelGateway


def experiment_gateway(route: str, params: dict[str, Any], budget: float) -> ModelGateway:
    profile = "bedrock" if route.startswith("bedrock:") else "local"
    base = load_settings(profile=profile).gateway
    if route not in base.models:
        raise ValueError(f"{route} needs a priced embedding model entry in the {profile} profile")
    spec = base.models[route]
    config = GatewayConfig(
        providers=base.providers,
        models={route: spec.model_copy(update={"params": spec.params | params})},
        tasks={"embed": (route,)},
        max_spend_usd=budget,
    )
    env = dict(os.environ)
    env[MAX_SPEND_ENV] = str(min(budget, max_spend(config, env)))
    return build_gateway(config, environ=env)


@dataclass
class EvalWorld:
    system: KnowledgeSystem
    sources: Sources
    outage: Outage


@contextmanager
def loaded(
    gateway: ModelGateway | None = None, *, fixed: dict[str, StepChoice] | None = None
) -> Iterator[EvalWorld]:
    settings = load_settings(profile="local")
    if fixed:
        settings = settings.model_copy(update={"steps": settings.steps | fixed})
    write = choose(settings, offline=True)
    if gateway is not None:
        write = write.with_options("embedding experiment", t7_embed="model")
    # The worker may purge during a scenario. Never attach a developer's model cache.
    write = replace(write, model_cache=OptionChoice(option="in_memory"))
    with (
        TemporaryDirectory() as scratch,
        mock_client(DOCS) as docs,
        mock_client(HR) as hr,
        mock_client(IDP) as idp,
    ):
        db = Path(scratch) / "hr.sqlite"
        run_mock_command(DB, ["init", "--path", str(db)])
        outage = Outage(docs)
        with outage.client() as access_docs:
            system = KnowledgeSystem(
                docs,
                hr,
                idp,
                db,
                write=write,
                query=choose_query(settings),
                assistant=choose_assistant(settings, offline=True),
                tokens=StaticTokens(issued=()),
                gateway=gateway,
                access_docs=access_docs,
            )
            system.sync(listings=True)
            if system.pipeline.dead_letters or not system.stores.search.chunks():
                raise ValueError("the worker did not load the evaluation corpus")
            yield EvalWorld(system, Sources(docs, hr, idp), outage)
