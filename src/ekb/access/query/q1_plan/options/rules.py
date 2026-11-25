"""``q1_plan: rules``: one search per part of a question with several parts (S14).

"How many days of annual leave do I get? Can I work from home on Fridays?" asks
two things. Searched as one, the top 5 go to whichever part has the stronger
words, and the other part may get nothing. Searched as two, each part gets its
own ranking, and the query path merges them by rank (reciprocal rank fusion, as
Q3 merges its legs), so each part's best passages reach the top 5.

The rules, with no model:

- a part ends after a question mark, or at a semicolon or a line break;
- a part with no letter or digit is dropped, and a part repeated (ignoring case)
  is searched once;
- at most ``max_searches`` searches (3): the parts beyond the last are searched
  together with it, so no word of the question is lost;
- nothing to split: the question itself is the one search.

Not done by rules: translating a question, expanding acronyms or splitting on
"and". Those need a model (``rewrite_when_needed``, card 4.8).
"""

from __future__ import annotations

import re

from ekb.contracts import QueryPlan
from ekb.platform.registry import register

VERSION = 1
MAX_SEARCHES = 3

_BOUNDARY = re.compile(r"(?<=\?)|[;\r\n]")
_MEANINGFUL = re.compile(r"[^\W_]")


@register("q1_plan", "rules")
class RulesPlanner:
    def __init__(self, max_searches: int = MAX_SEARCHES) -> None:
        if max_searches < 1:
            raise ValueError("max_searches must be at least 1")
        self._max = max_searches

    @property
    def planner_id(self) -> str:
        return f"rules@{VERSION}(max_searches={self._max})"

    def plan(self, question: str) -> QueryPlan:
        parts = _parts(question)
        if len(parts) <= 1:
            single = parts[0] if parts else question
            return QueryPlan(question=question, searches=(single,), planner_id=self.planner_id)
        notes = [f"split into {min(len(parts), self._max)} searches, one per part of the question"]
        if len(parts) > self._max:
            joined = " ".join(parts[self._max - 1 :])
            notes.append(
                f"the question has {len(parts)} parts: parts {self._max} to {len(parts)} "
                "are searched together"
            )
            parts = [*parts[: self._max - 1], joined]
        return QueryPlan(
            question=question,
            searches=tuple(parts),
            notes=tuple(notes),
            planner_id=self.planner_id,
        )


def _parts(question: str) -> list[str]:
    """The question's parts, trimmed, meaningful, each once, in order."""
    seen: set[str] = set()
    parts: list[str] = []
    for raw in _BOUNDARY.split(question):
        part = " ".join(raw.split())
        if not _MEANINGFUL.search(part) or part.casefold() in seen:
            continue
        seen.add(part.casefold())
        parts.append(part)
    return parts
