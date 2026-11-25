"""Port for Q1 · Plan. Every option implements :class:`Planner`.

A planner turns one question into the searches that answer it
(:class:`~ekb.contracts.QueryPlan`). Q2 + Q3 run once per search, over the
chunks the asker may read; the query path merges their hits by rank before Q4
checks them.

A plan always holds at least one search, so a question is never dropped by
planning: when nothing can be split, the question itself is searched.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ekb.contracts import QueryPlan


@runtime_checkable
class Planner(Protocol):
    """Turns a question into the searches that answer it."""

    @property
    def planner_id(self) -> str:
        """The option, version and settings, e.g. ``rules@1(max_searches=3)``."""
        ...

    def plan(self, question: str) -> QueryPlan: ...
