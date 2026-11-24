"""Consuming the bus the safe way: handle one event, commit it, then the next.

Every consumer (the registry recorder now, the worker pipeline from S12)
follows the same loop, so it is written once here. It is plain code on top of
the :class:`EventBus` port, so it works with every bus option.
"""

from __future__ import annotations

from collections.abc import Callable

from ekb.changelog.bus.port import Delivery, EventBus

Handler = Callable[[Delivery], object]
"""Processes one delivery; its return value is ignored. Raising means: not done, deliver again."""


def drain(bus: EventBus, group: str, handle: Handler, *, batch: int = 100) -> int:
    """Hand every pending event of ``group`` to ``handle``, in order; return how many.

    Each event is committed only after ``handle`` returns, so a crash never
    loses an event. If ``handle`` raises, the drain stops and the error
    propagates: that event and everything after it stay pending and are
    delivered again next time. Nothing is skipped, because the next event could
    be a delete. (An event that always fails would block forever; production
    moves it to a dead-letter queue after a few attempts.)
    """
    handled = 0
    while deliveries := bus.poll(group, batch):
        for delivery in deliveries:
            handle(delivery)
            bus.commit(group, delivery)
            handled += 1
    return handled
