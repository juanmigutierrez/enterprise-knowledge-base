"""SQLite response cache: survives restarts, so re-processing during development is free (D16 #1).

One file (``.cache/ekb/model-cache.sqlite`` by default, git-ignored), two tables:

- ``replies``: cache key -> the reply (JSON) and when it was made;
- ``tags``: which documents asked for each reply, so a deleted document's
  replies can be dropped (:meth:`forget`) while a reply another document still
  uses is kept;
- ``quotes`` (S16): which documents each reply's prompt quotes, such as the
  passages an answer was written from, so a reply goes as soon as any one of
  them is deleted. The table is created in an existing file on first use.

``path=":memory:"`` gives a private in-memory database, handy in tests.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Sequence
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
CREATE TABLE IF NOT EXISTS quotes (
    key          TEXT NOT NULL REFERENCES replies(key) ON DELETE CASCADE,
    document_key TEXT NOT NULL,
    PRIMARY KEY (key, document_key)
);
CREATE INDEX IF NOT EXISTS quotes_by_document ON quotes(document_key);
"""


@register("model_cache", "sqlite")
class SqliteCache:
    def __init__(self, path: str = MEMORY) -> None:
        if path != MEMORY:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA foreign_keys = ON")
        # The schema update and removal must commit together, including across openers.
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            for statement in _SCHEMA.strip().rstrip(";").split(";"):
                self._db.execute(statement)
            if self._db.execute("PRAGMA user_version").fetchone()[0] == 0:
                # A development build may already have added quotes to some S15 rows.
                self._db.execute(
                    "DELETE FROM replies WHERE key NOT IN (SELECT key FROM tags) "
                    "AND key NOT IN (SELECT key FROM quotes)"
                )
                self._db.execute("PRAGMA user_version = 1")
        self.guard = (
            threading.Lock() if path == MEMORY else FileLock(str(Path(path).resolve()) + ".lock")
        )
        self._lock = threading.Lock()

    def get(self, key: str) -> CachedReply | None:
        with self._lock:
            row = self._db.execute("SELECT entry FROM replies WHERE key = ?", (key,)).fetchone()
        return CachedReply.model_validate_json(row[0]) if row else None

    def put(
        self,
        key: str,
        entry: CachedReply,
        document_key: str | None = None,
        quotes: Sequence[str] = (),
    ) -> None:
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
            self._db.executemany(
                "INSERT OR IGNORE INTO quotes (key, document_key) VALUES (?, ?)",
                [(key, quoted) for quoted in dict.fromkeys(quotes)],
            )

    def tag(self, key: str, document_key: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO tags (key, document_key) "
                "SELECT key, ? FROM replies WHERE key = ?",
                (document_key, key),
            )

    def quote(self, key: str, document_keys: Sequence[str]) -> None:
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR IGNORE INTO quotes (key, document_key) "
                "SELECT key, ? FROM replies WHERE key = ?",
                [(quoted, key) for quoted in dict.fromkeys(document_keys)],
            )

    def forget(self, document_key: str) -> int:
        with self._lock, self._db:
            quoting = [
                row[0]
                for row in self._db.execute(
                    "SELECT key FROM quotes WHERE document_key = ?", (document_key,)
                )
            ]
            dropped = 0
            for key in quoting:  # the cascade removes their tags and quotes
                dropped += self._db.execute("DELETE FROM replies WHERE key = ?", (key,)).rowcount
            keys = [
                row[0]
                for row in self._db.execute(
                    "SELECT key FROM tags WHERE document_key = ?", (document_key,)
                )
            ]
            self._db.execute("DELETE FROM tags WHERE document_key = ?", (document_key,))
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
