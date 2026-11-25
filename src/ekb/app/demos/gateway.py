"""S7 · Model gateway: every model call routed, reused, guarded and costed.

``ekb demo gateway`` makes a handful of real calls through the free tiers in
``config/default.yaml``, with the keys from ``.env``::

    uv run --env-file .env ekb demo gateway        # about 5 requests of your free quotas
    uv run ekb demo gateway --fake                 # the same story offline, nothing kept

It asks for a one-line context header for a passage of the annual-leave policy
(what S8 will do for every chunk), then the same call again (from the cache),
then small changes that each make a new call, another task on another chain of
models, a failover, and a restart. It ends with what the cost log recorded and
where every free quota stands.

The cache is a fresh SQLite file for each run, so the story always starts with a
miss; the cost log is the real one (``.cache/ekb/model-calls.jsonl``), so the
requests count against the quotas and the inspector's **Model gateway** tab shows
them. ``--fake`` keeps nothing.
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from ekb.app.demos._app import demo_app
from ekb.app.demos._mocks import Echo, fail
from ekb.app.gateway import (
    FreeRequests,
    build_client,
    build_cost_log,
    build_gateway,
    openrouter_free_requests,
    provider_status,
)
from ekb.platform.config import load_settings
from ekb.platform.gateway.config import GatewayConfig, ModelSpec, ProviderSpec, Quota
from ekb.platform.gateway.errors import GatewayError, NoModelAvailableError
from ekb.platform.gateway.gateway import ModelGateway, utc_now
from ekb.platform.gateway.models import CallRecord, GatewayReply
from ekb.platform.gateway.options.cache_sqlite import SqliteCache
from ekb.platform.gateway.options.log_jsonl import JsonlCostLog
from ekb.platform.gateway.port import CostLog, ModelClient
from ekb.platform.gateway.usage import all_quota_usage, spent_usd, totals

WIDTH = 88
HEADER, ANSWER = "context_header", "answer"
VERSION = "demo-header@1"
MAX_TOKENS = 400
"""Generous on purpose: some free models think before they answer, and thinking
counts as output."""

TITLE = "Annual leave policy"
CARRY = (
    "Carrying leave over",
    "You may carry up to 5 unused days into the next year. Carried-over days must be "
    "used by 31 March; after that they are lost unless local law says otherwise.",
)
BOOKING = (
    "Booking leave",
    "Request leave in the HR portal at least two weeks before the first day off. Your "
    "manager approves or declines within three working days.",
)
"""Two passages of HR-PL-01 (synthetic data, sources/synthetic_data/hr_docs)."""

SYSTEM = (
    "You write a one-line context header for a passage of a company HR policy: which "
    "policy and section it is from, and what it says, in at most 25 words. Reply with "
    "the line only, in the language of the passage."
)
QUESTION = "How many unused leave days can I carry into next year, and until when?"

FAKE_CONFIG = GatewayConfig(
    providers={"fake": ProviderSpec()},
    quotas={
        "fake_small": Quota(per_minute=60, per_day=100),
        "fake_large": Quota(per_minute=60, per_day=100),
    },
    models={
        "fake:fake-small": ModelSpec(quota="fake_small"),
        "fake:fake-large": ModelSpec(quota="fake_large"),
    },
    tasks={
        HEADER: ("fake:fake-small", "fake:fake-large"),
        ANSWER: ("fake:fake-large", "fake:fake-small"),
    },
)
"""``--fake``: two offline models, so routing, the cache and failover still show."""

KEY_VARIABLES = "GOOGLE_API_KEY, GROQ_API_KEY, MISTRAL_API_KEY or OPENROUTER_API_KEY"


class DemoSetupError(RuntimeError):
    """No provider can be called: the demo says how to fix it instead of failing later."""


def passage_prompt(section: str, text: str) -> str:
    return f"Policy: {TITLE}\nSection: {section}\nPassage:\n{text}"


@dataclass
class Story:
    """What one run of the demo did, for the summary and the tests."""

    config: GatewayConfig
    log: CostLog
    workdir: Path
    first_record: int
    replies: dict[str, GatewayReply] = field(default_factory=dict)
    failover: str = ""

    def records(self) -> list[CallRecord]:
        return self.log.records()[self.first_record :]

    def requests(self) -> int:
        return sum(1 for r in self.records() if r.requested)


# --------------------------------------------------------------------- output


def _say(echo: Echo, text: str) -> None:
    for line in textwrap.wrap(text, width=WIDTH, initial_indent="   ", subsequent_indent="   "):
        echo(line)


def _when(moment: datetime) -> str:
    """UTC, and the local time of the machine running the demo."""
    return f"{moment:%Y-%m-%d %H:%M} UTC ({moment.astimezone():%H:%M} here)"


def _money(value: float | None) -> str:
    return "pending reconciliation" if value is None else f"${value:.6f}"


def _console_echo(line: str) -> None:
    try:
        typer.echo(line)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        typer.echo(line.encode(encoding, errors="backslashreplace").decode(encoding))


def _show(echo: Echo, story: Story, reply: GatewayReply) -> None:
    for step in reply.steps:
        if step.result != "ok" and step.result != "hit":
            _say(echo, f"- {step.candidate}: {step.result}")
    served = f" (served by {reply.served_model})" if reply.served_model else ""
    source = "from the cache, no request" if reply.cached else f"{reply.latency_ms} ms"
    echo(f"   -> {reply.candidate}{served}: {source}")
    echo(
        f"      tokens in/out {reply.input_tokens}/{reply.output_tokens}, "
        f"cost {_money(reply.cost_usd)}, key {reply.key[:12]}…"
    )
    text = reply.text.strip() or "(empty reply: the model spent its output on thinking)"
    for line in textwrap.wrap(text, width=WIDTH - 9, max_lines=4, placeholder=" …"):
        echo(f"      | {line}")
    echo(f"      requests sent so far in this run: {story.requests()}")


def _quotas(echo: Echo, config: GatewayConfig, log: CostLog, now: datetime) -> None:
    used = {config.models[ref].quota for ref in config.used_models()}
    for usage in all_quota_usage(config, log.records(), now):
        if usage.name not in used:
            continue
        day = (
            f"{usage.used_today}/{usage.per_day} today"
            if usage.per_day is not None
            else (f"{usage.used_today} today (no daily cap)")
        )
        minute = f", {usage.per_minute}/min" if usage.per_minute is not None else ""
        echo(f"   {usage.name:<20} {day}{minute}; resets {_when(usage.resets_at)}")


# --------------------------------------------------------------------- scenes


def run_gateway_demo(
    config: GatewayConfig,
    *,
    environ: Mapping[str, str],
    workdir: Path,
    log: CostLog,
    echo: Echo = _console_echo,
    clock: Callable[[], datetime] = utc_now,
    openrouter: Callable[[str], FreeRequests | None] = openrouter_free_requests,
) -> Story:
    """The whole story. ``workdir`` holds this run's cache; ``log`` is kept as given."""
    # This demo is about chat calls; T7's embedding model (S10) has its own demo.
    chat = [ref for ref in config.used_models() if config.models[ref].kind == "chat"]
    ready = [p for p in provider_status(config, environ, kind="chat") if p.ready]
    if not ready:
        raise DemoSetupError(
            f"no model provider has a key. Put {KEY_VARIABLES} in .env (see "
            "docs/walkthrough/S07-model-gateway.md, 'Get the free API keys'), then run "
            "`uv run --env-file .env ekb demo gateway`; or run it offline with --fake."
        )
    clients = {ref: build_client(config, ref, environ) for ref in chat}
    caches: list[SqliteCache] = []

    def gateway(cfg: GatewayConfig = config) -> ModelGateway:
        """A gateway on this run's cache file, as a new process would open it."""
        cache = SqliteCache(str(workdir / "model-cache.sqlite"))
        caches.append(cache)
        return build_gateway(
            cfg, environ=environ, clients=clients, cache=cache, log=log, clock=clock
        )

    story = Story(config=config, log=log, workdir=workdir, first_record=len(log.records()))
    try:
        main = gateway()
        _intro(story, echo, environ, openrouter, clock)
        _first_and_again(story, echo, main)
        _changes(story, echo, main)
        _another_task(story, echo, main)
        _failover(story, echo, gateway, clients)
        _restart(story, echo, gateway)
        _summary(story, echo, environ, openrouter, clock, main)
    finally:
        for cache in caches:
            cache.close()  # Windows cannot delete an open file
    return story


def _intro(
    story: Story,
    echo: Echo,
    environ: Mapping[str, str],
    openrouter: Callable[[str], FreeRequests | None],
    clock: Callable[[], datetime],
) -> None:
    config = story.config
    echo("S7 · Model gateway: every model call routed, reused, guarded and costed\n")
    echo("0. Steps never call a model directly. They name a task; the gateway picks the")
    echo("   model, reuses a reply already made, keeps within each free quota, and logs")
    echo("   tokens and cost (never the text) for every call.\n")
    echo("1. Who can answer (keys from the environment, never shown):")
    for status in provider_status(config, environ, kind="chat"):
        key = (
            f"{status.key_env} {'set' if status.ready else 'NOT set: skipped'}"
            if status.key_env
            else "no key needed"
        )
        echo(f"   {status.provider:<14} {key}")
    echo("   Free quotas before this run (counted from the cost log):")
    _quotas(echo, config, story.log, clock())
    _openrouter(echo, environ, openrouter)
    echo("")
    echo("2. Routes: each task tries its models in order (D15).")
    for task in (HEADER, ANSWER):
        echo(f"   {task:<15} {' -> '.join(config.tasks[task])}")
    echo("")


def _ask_header(
    gateway: ModelGateway, section: str, text: str, version: str = VERSION
) -> GatewayReply:
    return gateway.complete(
        HEADER,
        passage_prompt(section, text),
        system=SYSTEM,
        prompt_version=version,
        max_output_tokens=MAX_TOKENS,
        document_key="hr_docs:HR-PL-01",
    )


def _first_and_again(story: Story, echo: Echo, gateway: ModelGateway) -> None:
    echo(f"3. A context header for '{TITLE} > {CARRY[0]}' (what S8 does per chunk):")
    story.replies["first"] = first = _ask_header(gateway, *CARRY)
    _show(echo, story, first)
    echo("")
    echo("4. Exactly the same call again:")
    story.replies["again"] = again = _ask_header(gateway, *CARRY)
    _show(echo, story, again)
    same = "the same reply" if again.text == first.text else "a different reply (!)"
    _say(echo, f"Same key, {same}, no request. Re-processing an unchanged document costs nothing.")
    echo("")


def _changes(story: Story, echo: Echo, gateway: ModelGateway) -> None:
    echo("5. Only the prompt version changes, @1 -> @2 (the step now reads replies differently):")
    story.replies["version"] = reply = _ask_header(gateway, *CARRY, version="demo-header@2")
    _show(echo, story, reply)
    _say(
        echo,
        "The same text is sent, yet the key is new: a reply made for version 1 is not "
        "reused by version 2. (New wording would change the key by itself.)",
    )
    echo("")
    echo("6. The passage changes by one character ('31 March' -> '31 march'):")
    changed = CARRY[1].replace("31 March", "31 march")
    story.replies["changed"] = reply = _ask_header(gateway, CARRY[0], changed)
    _show(echo, story, reply)
    _say(echo, "The key is a hash of everything sent, so any edit is a new call.")
    echo("")


def _another_task(story: Story, echo: Echo, gateway: ModelGateway) -> None:
    echo(f"7. Another task, '{ANSWER}', on its own chain of stronger models:")
    story.replies["answer"] = reply = gateway.complete(
        ANSWER,
        f"{passage_prompt(*CARRY)}\n\nQuestion: {QUESTION}",
        system="Answer from the passage only, in one sentence.",
        prompt_version="demo-answer@1",
        max_output_tokens=MAX_TOKENS,
    )
    _show(echo, story, reply)
    echo("")


def _failover(
    story: Story,
    echo: Echo,
    gateway: Callable[[GatewayConfig], ModelGateway],
    clients: Mapping[str, ModelClient],
) -> None:
    config = story.config
    chain = config.tasks[HEADER]
    first = next((ref for ref in chain if clients[ref].check() is None), chain[0])
    quota_name = config.models[first].quota
    echo(f"8. Failover: a gateway where {first}'s daily cap is 0, as if its quota were")
    echo("   used up (a demo setting: no request is made to it), asks about another passage.")
    if quota_name is None:
        _say(echo, f"{first} has no quota to use up: nothing to show.")
        echo("")
        return
    spent = config.quotas[quota_name].model_copy(update={"per_day": 0})
    exhausted = config.model_copy(update={"quotas": {**config.quotas, quota_name: spent}})
    try:
        story.replies["failover"] = reply = _ask_header(gateway(exhausted), *BOOKING)
    except NoModelAvailableError as exc:
        story.failover = str(exc)
        _say(echo, f"No other model is left to try, so the call fails, saying why: {exc}")
        echo("")
        return
    story.failover = reply.candidate
    _show(echo, story, reply)
    _say(echo, "The used-up model was skipped without a request; the next one with a key answered.")
    echo("")


def _restart(story: Story, echo: Echo, gateway: Callable[[], ModelGateway]) -> None:
    echo("9. A new gateway on the same cache file, as after a restart, asks scene 3 again:")
    story.replies["restart"] = reply = _ask_header(gateway(), *CARRY)
    _show(echo, story, reply)
    _say(echo, "The SQLite cache outlives the process: re-ingesting tomorrow is free too.")
    echo("")


def _summary(
    story: Story,
    echo: Echo,
    environ: Mapping[str, str],
    openrouter: Callable[[str], FreeRequests | None],
    clock: Callable[[], datetime],
    gateway: ModelGateway,
) -> None:
    records = story.records()
    hits = sum(1 for r in records if r.outcome == "hit")
    echo(
        f"10. The cost log: {len(records)} records this run, "
        f"{story.requests()} requests, {hits} hits."
    )
    for row in totals(records):
        echo(f"   {row.task:<15} {row.candidate}")
        echo(
            f"   {'':<15} {row.requests} requests, {row.hits} hits, {row.failures} failed, "
            f"tokens {row.input_tokens}/{row.output_tokens}, {_money(row.cost_usd)}"
        )
    echo("   Free quotas after this run:")
    _quotas(echo, story.config, story.log, clock())
    _openrouter(echo, environ, openrouter)
    spent = spent_usd(story.log.records())
    echo(
        f"   Spend on paid models, all runs: {_money(spent)} "
        f"of the ${gateway.max_spend_usd:.2f} cap."
    )
    where = getattr(story.log, "path", None)
    if isinstance(where, Path) and not where.is_relative_to(story.workdir):
        echo(f"   Kept in {where}: see it in `ekb inspector`, tab 'Model gateway'.")
    else:
        echo("   This run's cost log is thrown away with its cache (nothing counts).")
    echo("   Next: S8 writes a context header for every chunk through this gateway.")


def _openrouter(
    echo: Echo, environ: Mapping[str, str], openrouter: Callable[[str], FreeRequests | None]
) -> None:
    key = environ.get("OPENROUTER_API_KEY")
    if not key:
        return
    counter = openrouter(key)
    if counter is None:
        echo("   OpenRouter's own counter: could not be read.")
    else:
        echo(
            f"   OpenRouter's own counter: {counter.used}/{counter.limit} free requests "
            f"today, {counter.remaining} left (it also sees calls made outside ekb)."
        )


# ------------------------------------------------------------------------ CLI


@demo_app.command("gateway")
def gateway_demo(
    fake: Annotated[
        bool,
        typer.Option("--fake", help="Run offline on two fake models; nothing is kept."),
    ] = False,
) -> None:
    """S7 · Model gateway: real free-tier calls, cached, guarded against quotas, and costed."""
    try:
        with tempfile.TemporaryDirectory(prefix="ekb-gateway-demo-") as tmp:
            workdir = Path(tmp)
            if fake:
                config: GatewayConfig = FAKE_CONFIG
                log: CostLog = JsonlCostLog(str(workdir / "model-calls.jsonl"))
            else:
                config = load_settings().gateway
                log = build_cost_log(config.cost_log)
            run_gateway_demo(config, environ=os.environ, workdir=workdir, log=log)
    except (DemoSetupError, GatewayError, ValueError) as exc:
        raise fail(exc) from exc
