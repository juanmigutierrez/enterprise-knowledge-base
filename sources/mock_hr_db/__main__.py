"""Manage the mock HR database from the command line.

    python -m sources.mock_hr_db init    --path data/hr.sqlite
    python -m sources.mock_hr_db sql     --path data/hr.sqlite "UPDATE leave_balances SET ..."
    python -m sources.mock_hr_db changes --path data/hr.sqlite [--since 16]

``sql`` plays the HR application (it runs one statement and commits);
``changes`` plays the CDC reader. ``data/`` is ignored by git.
"""

from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

from sources.mock_hr_db.db import create_database, read_changes


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m sources.mock_hr_db",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create the database with the synthetic rows")
    init.add_argument("--path", type=Path, required=True)
    sql = commands.add_parser("sql", help="run one SQL statement and commit")
    sql.add_argument("--path", type=Path, required=True)
    sql.add_argument("statement")
    changes = commands.add_parser("changes", help="print the change log after a cursor")
    changes.add_argument("--path", type=Path, required=True)
    changes.add_argument("--since", type=int, default=0)
    args = parser.parse_args(argv)

    if args.command == "init":
        args.path.parent.mkdir(parents=True, exist_ok=True)
        create_database(args.path)
        print(f"created {args.path}")
        return
    if not args.path.exists():
        parser.error(f"{args.path} does not exist; run init first")
    with closing(sqlite3.connect(args.path)) as conn, conn:
        if args.command == "sql":
            cursor = conn.execute(args.statement)
            for row in cursor.fetchall():
                print(row)
            print(f"{cursor.rowcount} row(s) changed" if cursor.rowcount >= 0 else "done")
        else:
            for change in read_changes(conn, args.since):
                print(change.model_dump_json())


if __name__ == "__main__":
    main()
