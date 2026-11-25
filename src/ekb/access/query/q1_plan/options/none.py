"""``q1_plan: none``: the question as it is, one search (S14).

The baseline card 4.8 compares every planner with, and the ``ci`` profile's
choice: nothing is split, nothing is rewritten.
"""

from __future__ import annotations

from ekb.contracts import QueryPlan
from ekb.platform.registry import register

VERSION = 1


@register("q1_plan", "none")
class NoPlanner:
    @property
    def planner_id(self) -> str:
        return f"none@{VERSION}"

    def plan(self, question: str) -> QueryPlan:
        return QueryPlan(question=question, searches=(question,), planner_id=self.planner_id)
