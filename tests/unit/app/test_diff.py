"""Lifetime boundaries from connector publication through T2 redelivery."""

import pytest

from ekb.app.changelog import ChangeLog
from ekb.app.diff import diff_step, run_t2
from ekb.changelog.rawstore.port import OriginalNotFoundError
from ekb.contracts import DiffAction
from ekb.process.t2_diff.step import DiffResult
from tests.unit.changelog.factories import delete, upsert


def test_retried_delete_removes_every_old_original_and_preserves_reused_version() -> None:
    log = ChangeLog.in_memory()
    key = "hr_docs:page"
    second = log.store_original(key, "2", b"second")
    first = log.store_original(key, "1", b"first")
    orphan = log.store_original(key, "orphan", b"unpublished")
    log.publish(upsert("page", "1", raw_pointer=first))
    log.publish(upsert("page", "2", raw_pointer=second))
    log.publish(delete("page", "2"))
    recreated = log.store_original(key, "1", b"recreated")
    with pytest.raises(ValueError, match="closed lifetime"):
        log.publish(upsert("page", "1", raw_pointer=first))
    log.publish(upsert("page", "1", raw_pointer=recreated))

    def fail_on_delete(result: DiffResult) -> None:
        if result.decision.action is DiffAction.PURGE:
            assert result.purged == 3
            raise RuntimeError("downstream unavailable")

    with pytest.raises(RuntimeError, match="downstream unavailable"):
        run_t2(log, diff_step(log, fail_on_delete))
    for pointer in [first, second, orphan]:
        with pytest.raises(OriginalNotFoundError):
            log.rawstore.get(pointer)

    retried: list[DiffResult] = []
    assert run_t2(log, diff_step(log, retried.append)) == 2
    assert [(r.decision.action, r.purged) for r in retried] == [
        (DiffAction.PURGE, 0),
        (DiffAction.PROCESS, 0),
    ]
    record = log.registry.get(key)
    assert record is not None
    assert record.raw_pointer == recreated
    assert log.rawstore.get(recreated) == b"recreated"
    log.publish(delete("page", "1"))
    assert run_t2(log, diff_step(log)) == 1
    assert log.rawstore.versions(key) == []
