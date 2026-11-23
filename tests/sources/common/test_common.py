"""The shared building blocks used by every mock."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sources.common.directory import Directory, UnknownPrincipalError
from sources.common.factory import create_service_app
from sources.common.feed import ChangeFeed
from sources.common.seed import SeedError, read_yaml, split_front_matter


class _Entry(BaseModel):
    seq: int
    note: str


def _entry(seq: int, *, note: str) -> _Entry:
    return _Entry(seq=seq, note=note)


def test_feed_numbers_entries_and_resumes() -> None:
    feed: ChangeFeed[_Entry] = ChangeFeed()
    assert feed.cursor == 0
    for note in ("a", "b", "c"):
        feed.append(partial(_entry, note=note))
    assert feed.cursor == 3
    assert [e.note for e in feed.since(1)] == ["b", "c"]
    assert feed.since(3) == []


def test_feed_refuses_an_entry_out_of_order() -> None:
    feed: ChangeFeed[_Entry] = ChangeFeed()
    with pytest.raises(ValueError, match="not the next one"):
        feed.append(lambda seq: _Entry(seq=seq + 1, note="x"))


def test_directory_rejects_user_in_unknown_group(tmp_path: Path) -> None:
    path = tmp_path / "directory.yaml"
    path.write_text("groups: {a: A}\nusers: {x: {name: X, location: Milan, groups: [b]}}\n")
    with pytest.raises(UnknownPrincipalError, match="'x'"):
        Directory.load(path)


def test_directory_rejects_duplicate_full_names(tmp_path: Path) -> None:
    path = tmp_path / "directory.yaml"
    path.write_text(
        "groups: {a: A}\nusers:\n"
        "  x: {name: Same Name, location: Milan, groups: [a]}\n"
        "  y: {name: Same Name, location: Madrid, groups: [a]}\n"
    )
    with pytest.raises(ValueError, match="unique"):
        Directory.load(path)


def test_directory_check() -> None:
    directory = Directory.load()
    directory.check(users=["alice"], groups=["hr-team"])
    with pytest.raises(UnknownPrincipalError, match="mallory"):
        directory.check(users=["mallory"])


def test_read_yaml_needs_a_mapping(tmp_path: Path) -> None:
    path = tmp_path / "x.yaml"
    path.write_text("- a\n- b\n")
    with pytest.raises(SeedError, match="mapping"):
        read_yaml(path)


def test_split_front_matter() -> None:
    meta, body = split_front_matter("---\nid: X\n---\n\n# Title\n")
    assert meta == {"id": "X"}
    assert body == "# Title\n"


class _MissingError(KeyError):
    pass


def test_service_app_has_health_and_maps_errors() -> None:
    app = create_service_app("t", errors={_MissingError: 404, ValueError: 409})

    @app.get("/missing")
    def missing() -> None:
        raise _MissingError("thing 1 not found")

    @app.get("/conflict")
    def conflict() -> None:
        raise ValueError

    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/missing").json() == {"detail": "thing 1 not found"}  # no KeyError quotes
    response = client.get("/conflict")
    assert (response.status_code, response.json()) == (409, {"detail": "ValueError"})


def test_clock_is_utc() -> None:
    from sources.common.clock import utc_now

    assert utc_now().tzinfo is UTC
    assert utc_now() <= datetime.now(UTC)
