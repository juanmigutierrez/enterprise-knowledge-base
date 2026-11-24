"""Both caches and both cost logs keep the same promises; the file-backed ones survive a restart."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ekb.platform.gateway.models import CachedReply, CallRecord, ModelReply
from ekb.platform.gateway.options.cache_memory import InMemoryCache
from ekb.platform.gateway.options.cache_sqlite import SqliteCache
from ekb.platform.gateway.options.log_jsonl import JsonlCostLog
from ekb.platform.gateway.options.log_memory import InMemoryCostLog
from ekb.platform.gateway.port import CostLog, ResponseCache

AT = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def entry(text: str = "Annual leave policy > Carrying leave over") -> CachedReply:
    reply = ModelReply(text=text, input_tokens=40, output_tokens=9, served_model="m")
    return CachedReply(candidate="fake:one", reply=reply, created_at=AT)


def record(n: int, outcome: str = "ok") -> CallRecord:
    return CallRecord.model_validate(
        {
            "at": AT,
            "task": "hea\xe2der",
            "candidate": "fake:one",
            "prompt_version": "v1",
            "key": f"{n:064x}",
            "outcome": outcome,
            "quota": "one_q",
            "input_tokens": n,
        }
    )


CACHES: dict[str, Callable[[Path], ResponseCache]] = {
    "in_memory": lambda _: InMemoryCache(),
    "sqlite": lambda tmp: SqliteCache(str(tmp / "cache" / "replies.sqlite")),
}
LOGS: dict[str, Callable[[Path], CostLog]] = {
    "in_memory": lambda _: InMemoryCostLog(),
    "jsonl": lambda tmp: JsonlCostLog(str(tmp / "logs" / "calls.jsonl")),
}


@pytest.fixture(params=sorted(CACHES))
def cache(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ResponseCache]:
    built = CACHES[request.param](tmp_path)
    yield built
    if isinstance(built, SqliteCache):
        built.close()


@pytest.fixture(params=sorted(LOGS))
def log(request: pytest.FixtureRequest, tmp_path: Path) -> CostLog:
    return LOGS[request.param](tmp_path)


# ------------------------------------------------------------------------ caches


def test_a_cache_returns_what_was_put(cache: ResponseCache) -> None:
    cache.put("k1", entry())

    assert cache.get("k1") == entry()
    assert cache.get("k2") is None
    assert cache.size() == 1


def test_forget_drops_an_entry_only_when_no_live_document_uses_it(cache: ResponseCache) -> None:
    cache.put("shared", entry("shared"), "hr_docs:A")
    cache.tag("shared", "hr_docs:B")  # B reused it
    cache.put("only_a", entry("only a"), "hr_docs:A")
    cache.put("untagged", entry("no document"))

    assert cache.forget("hr_docs:A") == 1  # only_a; shared still serves B
    assert cache.get("only_a") is None
    assert cache.get("shared") is not None
    assert cache.forget("hr_docs:B") == 1
    assert cache.get("shared") is None
    assert cache.get("untagged") is not None  # never tied to a document
    assert cache.forget("hr_docs:never-seen") == 0


def test_replacing_a_reply_preserves_previous_document_tags(cache: ResponseCache) -> None:
    cache.put("shared", entry("old"), "hr_docs:A")
    cache.put("shared", entry("new"), "hr_docs:B")
    assert cache.forget("hr_docs:B") == 0
    assert cache.get("shared") == entry("new")
    assert cache.forget("hr_docs:A") == 1


def test_tagging_a_missing_entry_does_nothing(cache: ResponseCache) -> None:
    cache.tag("ghost", "hr_docs:A")

    assert cache.forget("hr_docs:A") == 0
    assert cache.size() == 0


def test_the_sqlite_cache_survives_a_restart(tmp_path: Path) -> None:
    path = str(tmp_path / "nested" / "dir" / "replies.sqlite")
    first = SqliteCache(path)
    first.put("k1", entry(), "hr_docs:A")
    first.close()

    reopened = SqliteCache(path)

    assert reopened.get("k1") == entry()
    assert reopened.forget("hr_docs:A") == 1  # the tags came back too
    reopened.close()


# -------------------------------------------------------------------------- logs


def test_a_log_keeps_records_in_order(log: CostLog) -> None:
    for n in range(3):
        log.append(record(n))

    assert [r.input_tokens for r in log.records()] == [0, 1, 2]


def test_the_jsonl_log_survives_a_restart_and_sees_other_writers(tmp_path: Path) -> None:
    path = str(tmp_path / "calls.jsonl")
    demo, inspector = JsonlCostLog(path), JsonlCostLog(path)
    demo.append(record(1))
    assert len(inspector.records()) == 1

    demo.append(record(2))  # another process appends

    assert [r.input_tokens for r in inspector.records()] == [1, 2]
    assert [r.input_tokens for r in JsonlCostLog(path).records()] == [1, 2]


def test_the_jsonl_log_writes_one_lf_terminated_line_per_record(tmp_path: Path) -> None:
    log = JsonlCostLog(str(tmp_path / "calls.jsonl"))
    log.append(record(1))
    log.append(record(2, "hit"))

    raw = log.path.read_bytes()

    assert raw.count(b"\n") == 2
    assert b"\r" not in raw


def test_a_cut_short_line_is_skipped_and_the_next_record_starts_fresh(tmp_path: Path) -> None:
    log = JsonlCostLog(str(tmp_path / "calls.jsonl"))
    log.append(record(1))
    with log.path.open("ab") as file:
        file.write(b'{"at": "2026-11-24T12:00:00Z", "task": "hea\xe2')  # killed mid-write

    log.append(record(2))

    assert [r.input_tokens for r in log.records()] == [1, 2]
    assert log.skipped == 1


def test_legacy_provider_message_is_hidden_when_read(tmp_path: Path) -> None:
    log = JsonlCostLog(str(tmp_path / "calls.jsonl"))
    log.append(
        record(1).model_copy(update={"error": "SDKError (HTTP 400): synthetic document text"})
    )
    assert log.records()[0].error == "SDKError (HTTP 400)"


def test_a_missing_log_file_is_an_empty_log(tmp_path: Path) -> None:
    assert JsonlCostLog(str(tmp_path / "none.jsonl")).records() == []
