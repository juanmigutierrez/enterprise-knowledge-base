"""Score independent frozen chunk judgements against the production ranking legs."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, Field

from ekb.access.query.q3_retrieve.fusion import fuse
from ekb.app.system import KnowledgeSystem
from ekb.contracts import Hit
from ekb.stores.search.port import Scored

Mode = Literal["bm25", "vector", "hybrid_rrf"]


class Question(BaseModel, extra="forbid", frozen=True):
    id: str
    question: str
    language: Literal["it", "es", "en"]
    category: str
    expected_answer: str
    relevant_chunk_ids: tuple[str, ...] = Field(min_length=1)
    allowed_users: tuple[str, ...] = Field(min_length=1)


def questions(path: Path) -> list[Question]:
    rows = [
        Question.model_validate_json(line) for line in path.read_text(encoding="utf8").splitlines()
    ]
    if (
        not rows
        or len({r.id for r in rows}) != len(rows)
        or len({r.question for r in rows}) != len(rows)
    ):
        raise ValueError(f"{path}: expected unique, nonempty questions")
    return rows


def validate_labels(system: KnowledgeSystem, rows: Sequence[Question]) -> None:
    store = system.stores.search
    chunks = {c.chunk_id: c for c in store.chunks()}
    permissions = system.stores.permissions
    readable = {
        m.user_id: {c.chunk_id for c in store.readable_by(permissions.principals(m.user_id))}
        for m in permissions.memberships()
    }
    for row in rows:
        keys = set(row.relevant_chunk_ids)
        if not keys <= chunks.keys():
            raise ValueError(f"{row.id}: unknown chunk")
        if {u for u, ids in readable.items() if keys <= ids} != set(row.allowed_users):
            raise ValueError(f"{row.id}: reader labels disagree with published ACLs")
        evidence = " ".join(chunks[k].text for k in keys).casefold()
        if not all(part.casefold() in evidence for part in row.expected_answer.split(" + ")):
            raise ValueError(f"{row.id}: expected answer missing from evidence")


class Score(BaseModel):
    id: str
    category: str
    language: str
    ranked: list[str]
    recall: float
    reciprocal_rank: float
    hit5: bool
    ms: float


def rank(system: KnowledgeSystem, user: str, question: str, mode: Mode, k: int) -> tuple[Hit, ...]:
    scope = system.knowledge.search.q2.scope(user)
    snapshot = system.stores.search.ranking_snapshot(scope.principals)
    keyword = snapshot.keyword(question, 20).hits if mode != "vector" else ()
    vector: tuple[Scored, ...] = ()
    if mode != "bm25":
        encoder = system.encoder
        if encoder is None:
            raise ValueError("a semantic comparison requires real embeddings")
        ranking = snapshot.nearest(encoder.encode(question), encoder.space, 20)
        if ranking.skipped or not ranking.hits:
            raise ValueError("incomplete vector ranking")
        vector = ranking.hits
    return fuse(keyword, vector)[:k]


def evaluate(system: KnowledgeSystem, rows: Sequence[Question], mode: Mode, k: int) -> list[Score]:
    result = []
    for row in rows:
        start = perf_counter()
        hits = rank(system, row.allowed_users[0], row.question, mode, k)
        ranked = [h.chunk.chunk_id for h in hits]
        relevant = set(row.relevant_chunk_ids)
        ranks = [i for i, key in enumerate(ranked, 1) if key in relevant]
        result.append(
            Score(
                id=row.id,
                category=row.category,
                language=row.language,
                ranked=ranked,
                recall=len(ranks) / len(relevant),
                reciprocal_rank=1 / min(ranks) if ranks else 0,
                hit5=any(i <= 5 for i in ranks),
                ms=(perf_counter() - start) * 1000,
            )
        )
    return result


def percentile(values: Sequence[float], q: float) -> float:
    return sorted(values)[max(0, math.ceil(len(values) * q) - 1)]


def paired_delta(left: Sequence[Score], right: Sequence[Score]) -> tuple[float, float]:
    if [s.id for s in left] != [s.id for s in right]:
        raise ValueError("paired bootstrap requires identical questions in order")
    diffs = [a.recall - b.recall for a, b in zip(left, right, strict=True)]
    rng = random.Random(18)  # noqa: S311
    samples = [mean(rng.choices(diffs, k=len(diffs))) for _ in range(1000)]
    return percentile(samples, 0.025), percentile(samples, 0.975)


def metrics(rows: Sequence[Score], k: int) -> list[str]:
    lines = [
        f"| Slice | n | Recall@{k} | MRR@{k} | Hit@5 | p95 ms |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label in ["all", *sorted({r.language for r in rows}), *sorted({r.category for r in rows})]:
        part = [r for r in rows if label in ("all", r.language, r.category)]
        lines.append(
            f"| {label} | {len(part)} | {mean(r.recall for r in part):.3f} | "
            f"{mean(r.reciprocal_rank for r in part):.3f} | {mean(r.hit5 for r in part):.3f} | "
            f"{percentile([r.ms for r in part], 0.95):.1f} |"
        )
    return lines
