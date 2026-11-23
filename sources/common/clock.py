"""An injectable clock.

Mocks stamp every change with a time. Passing the clock in, instead of calling
``datetime.now`` inside, lets tests control time exactly (and test boundary
cases such as two changes at the same instant).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)
