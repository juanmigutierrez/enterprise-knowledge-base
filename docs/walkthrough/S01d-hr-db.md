# S01d · Sources: the mock HR database

> Architecture: `docs/architecture.md` (box 1, operational databases; Postgres + Debezium in the full design). Plan: `docs/plan.md` §4.2, S1d. Option card: none. Related decisions: D3 (change data capture for databases), D7 (structured data is queried with SQL, not embedded).

## 1. In one paragraph
An operational database doesn't send events and has no API: applications just write rows. Its changes are captured from the database's own log (Postgres's write-ahead log, read by Debezium). That is **change data capture (CDC)**. It sees every insert, update **and delete**, in commit order, with the row before and after. S1d builds a small stand-in with SQLite: two tables, `office_headcount` and `leave_balances`, and triggers that write every change into a `cdc_log` table in Debezium's vocabulary. Rows like these are figures, not text, so later they are answered with SQL instead of search (D7).

## 2. Where it sits in the flow
```
[ S1d · mock HR database ]  →  S2a · CDC reader (T1)  →  change log  →  (optional S14b) metrics store + SQL
output: the cdc_log table, read with SQL after a cursor (seq)
        op = r (snapshot) / c (create) / u (update) / d (delete), before / after as JSON
```

## 3. What was built
| File | What it contains |
|---|---|
| `sources/mock_hr_db/schema.py` | The tables, the `cdc_log` table, the triggers and the snapshot query |
| `sources/mock_hr_db/db.py` | `create_database` (validated seed → tables → snapshot → triggers) and `read_changes` |
| `sources/mock_hr_db/__main__.py` | `init`, `sql` (plays the HR application), `changes` (plays the CDC reader) |
| `sources/synthetic_data/hr_db.yaml` | 4 offices (one closed), 12 leave balances for 2026 |
| `src/ekb/app/demos/s1d.py` | `ekb demo s1d` |
| `tests/sources/mock_hr_db/` | CDC tests and the command line |

**Shared code across the mocks (`sources/common/`)**, as decided for these steps:

| Module | Used by |
|---|---|
| `factory.py`: builds a mock's FastAPI app (`/health`, errors → status codes) and serves it on 127.0.0.1 | docs, HR API, identity provider |
| `feed.py`: append-only change feed with a resumable cursor | docs, identity provider |
| `seed.py`: YAML and front matter loaders, the data folder | all four |
| `clock.py`: injectable clock for deterministic tests | docs, HR API, identity provider |
| `directory.py`: the shared users and groups | docs, identity provider |

The database needs no factory or feed: SQLite is its server, and `cdc_log` is its feed.

## 4. How it works

**The triggers are the CDC.** Each table has three `AFTER` triggers that copy the changed row into `cdc_log` **in the same transaction** as the change:
```sql
CREATE TRIGGER leave_balances_cdc_update AFTER UPDATE ON leave_balances BEGIN
    INSERT INTO cdc_log (table_name, op, row_key, before, after) VALUES
    ('leave_balances', 'u', json_object('employee_id', NEW.employee_id, 'year', NEW.year),
     json_object(... OLD ...), json_object(... NEW ...));
END;
```
Two properties follow, and they are what real CDC gives you:
- **A rolled-back change leaves no entry**, and neither does one refused by a constraint: the log entry is undone with the data.
- **`seq` order is commit order**, because SQLite runs one writer at a time.

**Deletes are captured with their last state.** A `d` entry has `before` (the row as it was) and no `after`. Unlike the HR API (S1b), nothing disappears silently.

**Snapshot first, then changes.** `create_database` loads the seed rows, logs each one once as `op = r` (a snapshot read), and only *then* creates the triggers, so seed rows aren't logged twice. Debezium starts the same way: read every row once, then stream. A reader starting at `seq 0` gets the full state, then the changes.

**Bad seeds leave nothing behind.** Initialization validates the rows and builds the database in a temporary file. It publishes the finished database at the requested path only after all inserts and triggers succeed.

**Links to the other sources.** `leave_balances.employee_id` is the HR API's ID (`E-1001` = Alice Rossi); `office_headcount` matches the number of HR records per office (a cross-source test checks both).

**Personal rows.** As decided for this step, `leave_balances` stays in: each row is personal, so later steps let it be read only by that employee and `hr-team`. That is a row-level permission, something documents don't have, for the optional SQL path (S14b).

## 5. Options
None: the mock is not a pipeline step. Later: the CDC reader connector (S2a), the optional structured path to a metrics store with SQL answers (S14b), and Postgres + Debezium as an optional Docker option (S17).

## 6. What the tests prove
- **Snapshot:** 16 `r` entries, in key order, all with `after` and no `before`.
- **CDC:** insert, update and delete each log exactly one entry in commit order, with correct `before` / `after`; a statement touching 12 rows logs 12 entries.
- **Transactions:** a rollback and a constraint violation both leave no entry; a second connection sees nothing until the commit.
- **Cursor:** a reader resumes from its last `seq` and gets nothing new at the head.
- **Safety:** `init` refuses to overwrite a database; a bad seed (days taken over the total, a malformed ID, an unknown table) is refused and leaves no file.
- **Command line:** `init`, `sql` and `changes` work together; `changes` on a missing file fails clearly.
- **Cross-source:** headcount per office and leave-balance IDs match the HR records.

## 7. Run it yourself
```powershell
uv run ekb demo s1d
```
It creates the database in a temporary folder, shows the 16 snapshot entries, writes three changes in one transaction, rolls another back, and prints entries 17–19 with their before and after.

By hand (`data/` is ignored by git):
```powershell
uv run python -m sources.mock_hr_db init    --path data/hr.sqlite
uv run python -m sources.mock_hr_db sql     --path data/hr.sqlite "UPDATE office_headcount SET headcount = 7 WHERE office_id = 'MIL'"
uv run python -m sources.mock_hr_db changes --path data/hr.sqlite --since 16
```

## 8. Decisions and limits
- **SQLite triggers instead of a write-ahead log.** Same contract (every change, in commit order, before/after, transactional), different mechanism. Postgres + Debezium would read the real WAL; that is the optional S17 option.
- **The log grows for ever.** Real CDC logs are retained for a while and then truncated; the change log (S3) and raw store keep what we need.
- **No schema changes captured.** Debezium also reports `ALTER TABLE`; out of scope.
- **The demo writes to the source database directly**, playing the HR application. The product's CDC reader will only *read* `cdc_log`.

## 9. Check your understanding
1. The HR application deletes the Bologna office row. How does a CDC reader learn about it, and what does it get that the HR API (S1b) would never give?
2. Someone updates a leave balance and then rolls back. What appears in the change log, and why?
3. Why does `create_database` write the snapshot entries *before* creating the triggers?

<details>
<summary>Answers</summary>

1. From a `cdc_log` entry with `op = d`, `row_key = {"office_id": "BLQ"}` and `before` holding the row's last state. It is told about the delete explicitly and knows exactly what was removed; the HR API gives no signal at all.
2. Nothing. The trigger writes the log entry inside the same transaction as the change, so the rollback undoes both.
3. So each seed row is logged exactly once, as a snapshot read (`r`). With the triggers already in place, loading the seed would also log every row as an insert (`c`), and a reader would see each row twice.
</details>
