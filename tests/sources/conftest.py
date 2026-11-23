"""Clocks shared by the mock tests: control time instead of waiting for it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

START = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)


class TickingClock:
    """Each call is one second after the previous one: every change gets its own time."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


class ManualClock:
    """Time stands still until a test moves it: for boundary cases."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float = 1) -> datetime:
        self.now += timedelta(seconds=seconds)
        return self.now


@pytest.fixture
def ticking_clock() -> TickingClock:
    return TickingClock()


@pytest.fixture
def manual_clock() -> ManualClock:
    return ManualClock()
