"""SQLite response cache: survives restarts, so re-processing during development is free (D16 #1).

One file (``.cache/ekb/model-cache.sqlite`` by default, git-ignored), two tables:

- ``replies``: cache key -> the reply (JSON) and when it was made;
- ``tags``: which documents asked for each reply, so a deleted document's
  replies can be dropped (:meth:`forget`) while a reply another document still
  uses is kept.

``path=":memory:"`` gives a private in-memory database, handy in tests.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from filelock import FileLock

from ekb.platform.gateway.models import CachedReply
from ekb.platform.registry import register

MEMORY = ":memory:"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS replies (
    key        TEXT PRIMARY KEY,
    entry      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tags (
    key          TEXT NOT NULL REFERENCES replies(key) ON DELETE CASCADE,
    document_key TEXT NOT NULL,
    PRIMARY KEY (key, document_key)
);
CREATE INDEX IF NOT EXISTS tags_by_document ON tags(document_key);
"""


@register("model_cache", "sqlite")
class SqliteCache:
    def __init__(self, path: str = MEMORY) -> None:
        if path != MEMORY:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)
        self.guard = (
            threading.Lock() if path == MEMORY else FileLock(str(Path(path).resolve()) + ".lock")
        )
        self._lock = threading.Lock()

    def get(self, key: str) -> CachedReply | None:
        with self._lock:
            row = self._db.execute("SELECT entry FROM replies WHERE key = ?", (key,)).fetchone()
        return CachedReply.model_validate_json(row[0]) if row else None

    def put(self, key: str, entry: CachedReply, document_key: str | None = None) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO replies (key, entry, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET entry=excluded.entry, "
                "created_at=excluded.created_at",
                (key, entry.model_dump_json(), entry.created_at.isoformat()),
            )
            if document_key is not None:
                self._db.execute(
                    "INSERT OR IGNORE INTO tags (key, document_key) VALUES (?, ?)",
                    (key, document_key),
                )

    def tag(self, key: str, document_key: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO tags (key, document_key) "
                "SELECT key, ? FROM replies WHERE key = ?",
                (document_key, key),
            )

    def forget(self, document_key: str) -> int:
        with self._lock, self._db:
            keys = [
                row[0]
                for row in self._db.execute(
                    "SELECT key FROM tags WHERE document_key = ?", (document_key,)
                )
            ]
            self._db.execute("DELETE FROM tags WHERE document_key = ?", (document_key,))
            dropped = 0
            for key in keys:
                left = self._db.execute("SELECT 1 FROM tags WHERE key = ? LIMIT 1", (key,))
                if left.fetchone() is None:
                    dropped += self._db.execute(
                        "DELETE FROM replies WHERE key = ?", (key,)
                    ).rowcount
            return dropped

    def size(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM replies").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._db.close()
