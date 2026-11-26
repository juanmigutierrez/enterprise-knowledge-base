"""Run the four S18 option cards and keep each run's evidence together."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from statistics import mean
from typing import Any, Literal, Self
from uuid import uuid4

import httpx
import yaml
from pydantic import BaseModel, Field, model_validator

from ekb.app.demos._serve import serving
from ekb.app.eval_world import EvalWorld, experiment_gateway, loaded
from ekb.app.gateway import max_spend
from ekb.app.lifecycle import BY_ID, OLD_TRAVEL_KEY
from ekb.connect.receiver import DOCS_WEBHOOK_PATH, create_receiver_app
from ekb.evaluation.quality import (
    Mode,
    Score,
    evaluate,
    metrics,
    paired_delta,
    questions,
    rank,
    validate_labels,
)
from ekb.evaluation.scenarios import caller, permission_quality, permissions, propagation, require
from ekb.platform.config import StepChoice
from ekb.platform.gateway.config import GatewayConfig
from ekb.platform.gateway.errors import GatewayError
from ekb.platform.gateway.usage import spent_usd


class Route(BaseModel, extra="forbid"):
    option: Literal["model"] = "model"
    route: str
    params: dict[str, Any] = Field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.route + (" " + json.dumps(self.params, sort_keys=True) if self.params else "")


class Card(BaseModel, extra="forbid"):
    card: Literal["4.4", "4.5", "4.13", "4.14"]
    question: str
    step: Literal["t7_embed", "q3_retrieve"] | None = None
    corpus: Literal["hr_synthetic_v1"] = "hr_synthetic_v1"
    fixed: dict[str, StepChoice] = Field(default_factory=dict)
    gates: dict[Literal["leakage_rate", "deleted_content_rate"], Literal[0]] = Field(
        default_factory=dict
    )
    primary_metric: str | None = None
    secondary_metrics: tuple[
        Literal["mrr@10", "hit@5", "cost_per_1k_chunks_usd", "p95_latency_ms"], ...
    ] = ()
    decision_rule: (
        Literal["cheapest option within 2 points of the best primary metric (paired bootstrap CI)"]
        | None
    ) = None
    dataset: Literal["golden_v1"] = "golden_v1"
    top_k: int = Field(default=5, ge=5, le=20)
    modes: tuple[Mode, ...] = ()
    options: tuple[Route, ...] = ()
    max_spend_usd: float = Field(default=0, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_options(self) -> Self:
        if self.fixed.keys() - {"q3_retrieve", "t4_chunk"}:
            raise ValueError("unsupported fixed step")
        retrieval = self.fixed.get("q3_retrieve")
        if retrieval is not None:
            if retrieval.option != "hybrid_rrf" or retrieval.params.get("rerank", "none") != "none":
                raise ValueError("the embedding card fixes hybrid retrieval without a reranker")
            k = retrieval.params.get("top_k", self.top_k)
            if type(k) is not int or not 5 <= k <= 20:
                raise ValueError("fixed top_k must be between 5 and 20")
            self.top_k = k
            self.modes = ("hybrid_rrf",)
        if self.primary_metric and self.primary_metric != f"recall@{self.top_k}":
            raise ValueError("primary metric must match the retrieval depth")
        if self.card in ("4.4", "4.5") and (not self.options or not self.modes):
            raise ValueError("retrieval comparisons require modes and embedding routes")
        if len({r.label for r in self.options}) != len(self.options) or len(set(self.modes)) != len(
            self.modes
        ):
            raise ValueError("duplicate comparison option")
        return self


def run_card(path: Path, *, holdout: bool = False, paid: bool = False) -> tuple[Path, bool]:
    card = Card.model_validate(yaml.safe_load(path.read_text(encoding="utf8")))
    root = path.parent.parent
    results = root / "results"
    raw = results / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S")
    name = f"{stamp}-{path.stem}-{uuid4().hex[:6]}"
    report = results / (name + ".md")
    lines = [f"# Card {card.card} · {card.question}", ""]
    passed = True
    try:
        if card.card == "4.13":
            with loaded() as world:
                dataset = "holdout_v1" if holdout else card.dataset
                rows = questions(root / "datasets" / (dataset + ".jsonl"))
                lines.extend(permission_quality(world, rows))
                lines.extend(permissions(world))
        elif card.card == "4.14":
            propagation_comparison(lines)
        else:
            passed = compare_quality(card, root, raw, name, lines, holdout=holdout, paid=paid)
    except (ValueError, RuntimeError, OSError, httpx.HTTPError) as exc:
        passed = False
        lines.append(f"Run incomplete or gate failed: {exc}")
    lines.append("\nRun status: " + ("passed" if passed else "failed"))
    report.write_text("\n".join(lines) + "\n", encoding="utf8")
    return report, passed


def propagation_comparison(lines: list[str]) -> None:
    for webhook in (True, False):
        with loaded() as world:
            lines.append("")
            lines.append("## Webhook" if webhook else "## Poll by full listing")
            lines.append("")
            if webhook:
                receiver = create_receiver_app(world.system.connectors.webhook.receive)
                with serving(receiver) as server:
                    world.sources.docs.post(
                        "/subscriptions", json={"url": server.url + DOCS_WEBHOOK_PATH}
                    ).raise_for_status()
                    lines.extend(propagation(world, webhook=True))
            else:
                lines.extend(propagation(world))
                lines.extend(
                    [
                        "",
                        "For arrivals uniformly distributed within a polling interval, "
                        "the modeled p50/p95 schedule waits are 30/57 s (1 minute), "
                        "150/285 s (5 minutes), "
                        "and 450/855 s (15 minutes). "
                        "Add the measured active processing time above.",
                    ]
                )


def compare_quality(
    card: Card, root: Path, raw: Path, name: str, lines: list[str], *, holdout: bool, paid: bool
) -> bool:
    passed = True
    dataset = "holdout_v1" if holdout else card.dataset
    dataset_file = root / "datasets" / (dataset + ".jsonl")
    rows = questions(dataset_file)
    digest = hashlib.sha256(dataset_file.read_text(encoding="utf8").encode()).hexdigest()
    lines.append(f"Question-set SHA-256 (LF): {digest}")
    lines.extend([f"Dataset: {dataset} ({len(rows)} questions). Labels await human review.", ""])
    scores: dict[str, list[Score]] = {}
    costs: dict[str, float] = {}
    remaining = min(
        card.max_spend_usd, max_spend(GatewayConfig(max_spend_usd=card.max_spend_usd), os.environ)
    )
    for index, option in enumerate(card.options):
        if not option.route.startswith("ollama:") and not paid:
            lines.append(f"- {option.label}: unmeasured (hosted route requires --paid).")
            continue
        if paid and card.card == "4.4" and option.route.startswith("ollama:"):
            lines.append(f"- {option.label}: excluded from hosted-only embedding run.")
            continue
        gateway = experiment_gateway(option.route, option.params, remaining)
        try:
            with loaded(gateway, fixed=card.fixed) as world:
                system = world.system
                validate_labels(system, rows)
                chunks = system.stores.search.chunks()
                ingest_cost = spent_usd(gateway.log.records())
                cost = (
                    "unreported"
                    if ingest_cost is None
                    else f"USD {1000 * ingest_cost / len(chunks):.5f}"
                )
                width = 1 if gateway.config.models[option.route].int8 else 4
                dimensions = len(chunks[0].vector or ())
                lines += [
                    "",
                    f"## {option.label}",
                    f"{len(chunks)} published chunks. Logged ingestion cost per 1,000 "
                    f"chunks: {cost}.",
                    f"Embedding dimensions: {dimensions}. "
                    f"Packed payload per vector: {dimensions * width} bytes "
                    "(int8 or float32, excluding index overhead).",
                ]
                if card.card == "4.5" and system.encoder is not None:
                    for row in rows:
                        system.encoder.encode(row.question)
                    lines.append("Query embeddings were warmed once before timing each mode.")
                pending = {}
                for mode in card.modes:
                    label = f"{option.label} / {mode}"
                    samples = evaluate(system, rows, mode, card.top_k)
                    pending[label] = samples
                    with (raw / (name + f"-{index + 1}-{mode}.jsonl")).open(
                        "x", encoding="utf8"
                    ) as f:
                        for sample in samples:
                            f.write(sample.model_dump_json() + "\n")
                    lines += ["", f"### {mode}", "", *metrics(samples, card.top_k)]
                agent_gates(world, card.modes, card.top_k)
                lines.append(
                    "Agent gates: 0/1 restricted salary disclosures and "
                    "0/2 deleted policy disclosures."
                )
                scores.update(pending)
                if ingest_cost is not None:
                    costs.update(dict.fromkeys(pending, ingest_cost / len(chunks)))
        except (GatewayError, ValueError, RuntimeError, httpx.HTTPError) as exc:
            lines.append(f"Excluded {option.label}: {exc}")
            passed = False
        used = spent_usd(gateway.log.records())
        if used is None:
            lines.append("Paid calls paused pending cost reconciliation.")
            passed = False
            break
        remaining = max(0, remaining - used)
    for left, right in combinations(scores, 2):
        lo, hi = paired_delta(scores[left], scores[right])
        delta = mean(r.recall for r in scores[left]) - mean(r.recall for r in scores[right])
        lines.append(
            f"- Paired Recall@{card.top_k} difference ({left} minus {right}): "
            f"{delta:+.3f}, bootstrap 95% CI [{lo:+.3f}, {hi:+.3f}]."
        )
    if card.decision_rule and len(scores) > 1 and scores.keys() == costs.keys():
        quality = {label: mean(s.recall for s in samples) for label, samples in scores.items()}
        eligible = [
            label for label, value in quality.items() if value >= max(quality.values()) - 0.02
        ]
        cheapest = min(costs[label] for label in eligible)
        selected = [label for label in eligible if costs[label] == cheapest]
        lines.append("Lowest ingestion cost within two recall points: " + ", ".join(selected))
    if not scores:
        passed = False
    lines.append("A development run with provisional labels cannot select a production default.")
    return passed


def agent_gates(world: EvalWorld, modes: tuple[Mode, ...], k: int) -> None:
    system = world.system
    pay_query = "What is the salary midpoint for engineering in Italy?"
    for mode in modes:
        authorized = rank(system, "bob", pay_query, mode, k)
        denied_hits = rank(system, "alice", pay_query, mode, k)
        require(
            any(h.chunk.document_key == "hr_docs:HR-CMP-01" for h in authorized),
            f"{mode}: salary positive control was absent",
        )
        require(
            all(h.chunk.document_key != "hr_docs:HR-CMP-01" for h in denied_hits),
            f"{mode}: restricted salary candidate returned",
        )
    denied = system.assistant.ask(
        caller("alice"), "What is the salary midpoint for engineering in Italy?"
    )
    require(
        not any(
            marker in denied.model_dump_json()
            for marker in ("hr_docs:HR-CMP-01", "47,000", "Salary bands 2026")
        ),
        "restricted salary exposed",
    )
    hotel_query = "What was the hotel limit under the superseded 2024 business travel policy?"
    for mode in modes:
        hits = rank(system, "alice", hotel_query, mode, k)
        require(
            any(h.chunk.document_key == OLD_TRAVEL_KEY for h in hits),
            f"{mode}: historical travel positive control was absent",
        )
    BY_ID["delete_page"].apply(world.sources)
    for sync in [False, True]:
        if sync:
            system.sync(listings=True)
        for mode in modes:
            hits = rank(system, "alice", hotel_query, mode, k)
            scope = system.knowledge.search.q2.scope("alice")
            released = system.knowledge.q4.release(hotel_query, scope, hits)
            require(
                all(p.citation.document_key != OLD_TRAVEL_KEY for p in released.passages),
                f"{mode}: deleted passage released",
            )
        answer = system.assistant.ask(caller("alice"), hotel_query)
        require(
            not any(
                marker in answer.model_dump_json()
                for marker in (
                    OLD_TRAVEL_KEY,
                    "€120",
                    "Business travel policy (2024",
                )
            ),
            "deleted policy exposed",
        )
