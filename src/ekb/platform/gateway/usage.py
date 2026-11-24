"""Quota windows, spend and totals, all computed from the cost log.

Nothing here keeps state: the cost log is the only record of what was used, so
two processes sharing one log (the demo and the inspector) agree on the numbers,
and a restart forgets nothing. Every request counts, failed ones included:
OpenRouter counts failed attempts against the daily free limit too.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from ekb.platform.gateway.config import GatewayConfig, Quota
from ekb.platform.gateway.models import CallRecord

MINUTE = timedelta(minutes=1)


def day_start(now: datetime, zone: str) -> datetime:
    """The last midnight in ``zone`` before ``now``: when today's quota began."""
    local = now.astimezone(ZoneInfo(zone))
    return datetime.combine(local.date(), time(), tzinfo=local.tzinfo).astimezone(now.tzinfo)


def next_reset(now: datetime, zone: str) -> datetime:
    """The next midnight in ``zone``: when the daily quota resets."""
    local = now.astimezone(ZoneInfo(zone))
    tomorrow = local.date() + timedelta(days=1)
    return datetime.combine(tomorrow, time(), tzinfo=local.tzinfo).astimezone(now.tzinfo)


def requests_since(records: Iterable[CallRecord], quota: str, since: datetime) -> list[CallRecord]:
    """Requests counted against ``quota`` at or after ``since`` (cache hits are not requests)."""
    return [r for r in records if r.quota == quota and r.requested and r.at >= since]


class QuotaUsage(BaseModel):
    """Where one quota stands right now."""

    model_config = ConfigDict(frozen=True)

    name: str
    per_minute: int | None
    per_day: int | None
    resets: str
    used_last_minute: int
    used_today: int
    resets_at: datetime

    @property
    def day_left(self) -> int | None:
        return None if self.per_day is None else max(self.per_day - self.used_today, 0)


def quota_usage(
    name: str, quota: Quota, records: Sequence[CallRecord], now: datetime
) -> QuotaUsage:
    return QuotaUsage(
        name=name,
        per_minute=quota.per_minute,
        per_day=quota.per_day,
        resets=quota.resets,
        used_last_minute=len(requests_since(records, name, now - MINUTE)),
        used_today=len(requests_since(records, name, day_start(now, quota.resets))),
        resets_at=next_reset(now, quota.resets),
    )


def all_quota_usage(
    config: GatewayConfig, records: Sequence[CallRecord], now: datetime
) -> list[QuotaUsage]:
    return [quota_usage(name, q, records, now) for name, q in sorted(config.quotas.items())]


def spent_usd(records: Iterable[CallRecord]) -> float | None:
    """Everything the cost log says was spent, over its whole life."""
    spent = 0.0
    for record in records:
        if record.cost_usd is None:
            return None
        spent += record.cost_usd
    return spent


class Totals(BaseModel):
    """Calls of one task to one candidate."""

    model_config = ConfigDict(frozen=True)

    task: str
    candidate: str
    requests: int = 0
    hits: int = 0
    failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    """Tokens sent to and returned by providers (cache hits excluded: nothing was sent)."""
    cost_usd: float | None = 0.0


def totals(records: Iterable[CallRecord]) -> list[Totals]:
    """Per task and candidate, sorted by task then candidate."""
    rows: dict[tuple[str, str], Totals] = {}
    for r in records:
        row = rows.get((r.task, r.candidate)) or Totals(task=r.task, candidate=r.candidate)
        update = {
            "requests": row.requests + (1 if r.requested else 0),
            "hits": row.hits + (1 if r.outcome == "hit" else 0),
            "failures": row.failures + (1 if r.outcome not in ("hit", "ok") else 0),
            "cost_usd": row.cost_usd + r.cost_usd
            if row.cost_usd is not None and r.cost_usd is not None
            else None,
        }
        if r.requested:
            update["input_tokens"] = row.input_tokens + r.input_tokens
            update["output_tokens"] = row.output_tokens + r.output_tokens
        rows[(r.task, r.candidate)] = row.model_copy(update=update)
    return [rows[k] for k in sorted(rows)]
