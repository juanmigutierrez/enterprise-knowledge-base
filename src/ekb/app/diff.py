"""T2 wired to the change log (S4): consumer group ``t2_diff``.

T2 takes over the job of S3's stand-in worker (group ``registry``): it reads
every event in order, decides what it needs (process, fast lane, skip, purge),
purges a deleted item's originals and records the registry. The S3 demo keeps
the stand-in, so its replay story can still read every original.

The ``content_hash`` option is chosen here by hand; it is also the default in
``config/default.yaml``. Choosing it from config comes with the pipeline (S12),
which also replaces the do-nothing ``downstream`` with T3, T8 and the stores.
"""

from __future__ import annotations

from collections.abc import Callable

from ekb.app.changelog import ChangeLog
from ekb.changelog.bus.consume import drain
from ekb.changelog.bus.port import Delivery
from ekb.process.t2_diff.options.content_hash import ContentHashDiffer
from ekb.process.t2_diff.step import DiffResult, DiffStep, Downstream

T2_GROUP = "t2_diff"
"""T2's consumer group. It replaced S3's stand-in group ``registry``."""

OnResult = Callable[[Delivery, DiffResult], object]


def diff_step(changelog: ChangeLog, downstream: Downstream | None = None) -> DiffStep:
    """T2 over the change log's registry and raw store."""
    differ = ContentHashDiffer()
    if downstream is None:
        return DiffStep(differ, changelog.registry, changelog.rawstore)
    return DiffStep(differ, changelog.registry, changelog.rawstore, downstream)


def run_t2(changelog: ChangeLog, step: DiffStep, on_result: OnResult | None = None) -> int:
    """Drain T2's group: decide, act and record every pending event; return how many.

    ``on_result`` sees each result with its place in the bus, after it was recorded.
    """

    def handle(delivery: Delivery) -> None:
        result = step.handle(delivery.event)
        if on_result is not None:
            on_result(delivery, result)

    return drain(changelog.bus, T2_GROUP, handle)
