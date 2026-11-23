"""An append-only change feed with a resumable cursor.

Push sources (webhooks) and identity providers expose a feed like this. Each
entry gets the next ``seq``; a reader that remembers the last ``seq`` it saw
catches up with :meth:`ChangeFeed.since` and never misses or repeats an entry.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class Sequenced(Protocol):
    @property
    def seq(self) -> int: ...


class ChangeFeed[E: Sequenced]:
    """Entries ordered by ``seq`` (1, 2, 3 ...). Not thread-safe on its own:
    the owning store appends under its lock, together with the state change."""

    def __init__(self) -> None:
        self._entries: list[E] = []

    @property
    def cursor(self) -> int:
        """``seq`` of the latest entry, 0 when empty."""
        return self._entries[-1].seq if self._entries else 0

    def append(self, make: Callable[[int], E]) -> E:
        """Build the next entry with ``make(seq)`` and append it."""
        entry = make(self.cursor + 1)
        if entry.seq != self.cursor + 1:
            raise ValueError(f"entry seq {entry.seq} is not the next one ({self.cursor + 1})")
        self._entries.append(entry)
        return entry

    def since(self, seq: int) -> list[E]:
        """Entries after ``seq``, oldest first."""
        return [e for e in self._entries if e.seq > seq]
