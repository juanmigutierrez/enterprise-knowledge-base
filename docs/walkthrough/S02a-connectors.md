# S02a · Connect + T1 for every source type

> Architecture: `docs/architecture.md` (box 2: "Polling connector", "Debezium on Kafka Connect", "Identity sync", "T1 · Normalize"). Plan: `docs/plan.md` §4.2, S2a. Option card: `docs/option-evaluation.md` 4.14 (change propagation). Related decisions: D3 (one change-event contract; one canonical person ID; nightly full listing for deletes), D7 (structured data), D8 (permissions).

## 1. In one paragraph
S2 turned the docs tool's changes into `ChangeEvent`s. S2a does the same for the other three kinds of source, and they could hardly be more different. The HR record API pushes nothing and deletes without a trace, so you **poll** it and **list** it. The database has no API, so you read its **change log** (CDC). The identity provider holds no documents at all, but a group change there is a permission change everywhere. Four connectors, one event shape: nothing after T1 knows or cares where a change came from. Along the way S2a settles two things every source needs. **One person ID**: every person in an event is the identity provider's user ID, even when the source said `E-1001`. **Who changed it**: each event says who made the change, when the source knows.

## 2. Where it sits in the flow
```
docs tool    ──webhook / listing──▶ docs_webhook, docs_full_listing   (S2) ─┐
HR API       ──updated_since / list▶ hr_api_delta, hr_api_full_listing      ├─ T1 ─▶ ChangeEvent v2 ─▶ sink (S3: event bus)
HR database  ──cdc_log after seq───▶ hr_db_cdc                             │        originals  ─▶ OriginalStore (S3: raw store)
identity     ──users + feed────────▶ identity_sync                         ─┘
                                        ▲
                        PersonMap: E-1001 → alice (identity provider + HR API, 1-1 by full name)
```

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/contracts/events.py` | `ChangeEvent` **schema version 2**: `person_id` and `changed_by`; the contract now refuses a delete that carries a hash, an ACL or a person |
| `src/ekb/connect/people.py` | `PersonMap`: identity user IDs, HR employee → identity user, `unmapped`, `gaps()`, `coverage()` |
| `src/ekb/connect/normalize/` | T1 as one module per source: `docs.py` (from S2), `hr_api.py`, `hr_db.py`, `identity.py`, and `common.py` (canonical JSON, hash, delete rule, `UnknownChangeKindError`) |
| `src/ekb/connect/hr_api.py`, `identity_api.py`, `hr_db.py` | The connector's own clients: REST for the HR API, SCIM for the identity provider, read-only SQL for the database |
| `src/ekb/connect/options/hr_api_delta.py` | Delta polling with `updated_since`; the first poll is the initial load |
| `src/ekb/connect/options/hr_api_full_listing.py` | Compares the full listing with what is known: the only way to see an HR delete |
| `src/ekb/connect/options/hr_db_cdc.py` | Reads `cdc_log` after a cursor: snapshot, inserts, updates and deletes in commit order |
| `src/ekb/connect/options/identity_sync.py` | Cursor, listing, then the feed; every change is an upsert of one user |
| `sources/mock_docs/`, `mock_hr_api/`, `mock_idp/` | Writes accept `X-Actor: <user id>`; kept as `modified_by` / `updated_by` / the event's `actor` |
| `sources/mock_hr_db/schema.py`, `db.py` | A `changed_by` column the application fills in; the triggers carry it in `before` / `after` |
| `src/ekb/app/demos/t1.py` | `ekb demo t1 --all`; the S2 story now uses named people too |
| `tests/unit/connect/…`, `tests/sources/test_actors.py` | People, the four new connectors, the record rules, the mocks' actors |

## 4. How it works

**Every event is about one tracked item.** `document_key` (`source:source_id`) names it, and its events in order are its history: what happened, when (`occurred_at`), who did it (`changed_by`), and, for records about a person, whom it concerns (`person_id`).

| Source | `source_id` | `version` | ACL | `person_id` |
|---|---|---|---|---|
| HR API employee | `employees/E-1001` | `updated_at` | `all-staff` | `alice` |
| HR API team | `teams/T-ENG` | `updated_at` | `all-staff` | — |
| DB office row | `office_headcount/MIL` | CDC `seq` | `all-staff` | — |
| DB leave row | `leave_balances/E-1001/2026` | CDC `seq` | `alice` + `hr-team` | `alice` |
| Identity user | `users/alice` | feed `seq` (listing: the cursor) | `all-staff` | `alice` |

Each CDC round refreshes the HR and identity mapping and checks every live leave row. An owner change emits an `acl_change` even if nobody edited the database. Only `hr-team` retains access while the match is missing or ambiguous. The ACL event version includes the row sequence and the observation time. On resume from a bare cursor, the connector reconstructs the live leave rows and republishes their current ACLs once.

Versions differ by source; they only need to order one item's history.

**One person ID: `PersonMap`.** It loads the identity provider's users and the HR API's employees and matches them **1-1 by full name** (the S1b rule). Every person ID a connector writes goes through it:
```python
person = people.for_employee(record.id)  # E-1001 -> "alice"
changed_by = people.user(record.updated_by)  # "lucia" if the identity provider knows her
acl_users = people.users_in(page.acl.users)  # unknown users dropped
```
Nothing is mapped silently. On a miss it reloads both sources once (a joiner may have just arrived). If it still fails, the ID goes into `people.unmapped`, a warning is logged, and the caller **fails closed**: no `person_id`, no `changed_by`, and the person is left out of the ACL. A leave row whose employee can't be mapped is readable by `hr-team` only. `people.gaps()` is the double check over whole sources: every HR employee must have an identity user. The demo prints it, and a test fails if the synthetic company has a gap.

**Who changed it.** The mocks now record the writer, like the real tools do (SharePoint's "modified by", Workday's audit trail, Okta's actor):
- docs tool, HR API, identity provider: an optional `X-Actor` header on every write;
- database: real CDC doesn't know which application user wrote a row, so the table has a `changed_by` column the application fills in.

The connector checks each writer against the `PersonMap`. `changed_by` stays empty when nobody can know: seeded data, a delete found only by a listing, a database delete (the row is gone).

**HR API: delta for edits, listing for deletes.** The delta passes back the previous `as_of`:
```python
listing = self._api.list(collection, updated_since=self._since[collection])
...
self._since[collection] = listing.as_of  # moved only after every record is emitted
```
The filter is inclusive, so a record can come back twice; the repeat has the same version and hash, and T2 skips it. A deleted record never appears in any delta, so `hr_api_full_listing` compares every record with what is known. Unlike the docs listing, it needs no fetch: an HR listing carries the full records.

**Database: CDC.** `hr_db_cdc` opens the file read-only and reads `cdc_log` after the last `seq`: first the snapshot (`r`), then `c`, `u`, `d` in commit order. Nothing is ever missed, so no listing is needed. Each table has a rule in `normalize/hr_db.py` (who reads it, whom a row is about); a table without a rule is an error, not a skip.

**Identity: each user is one item.** Joiners, leavers and group changes are all **upserts** of the user's current record (groups and `active` included). A leaver is not a delete: the identity provider keeps them, inactive. The first sync reads the feed cursor, lists every user, then replays the feed from that cursor, so a change made during the listing is not lost. Which pages a group change opens or closes is permission resolution (T8, S11): the connector doesn't know which pages a group can read.

**Structured originals.** Records and rows are stored as canonical JSON (sorted keys) through the same `OriginalStore` as documents, and the hash is of exactly those bytes: the same record always has the same hash.

## 5. Options
Built now, all under step `connect`: `docs_webhook`, `docs_full_listing` (S2), `hr_api_delta`, `hr_api_full_listing`, `hr_db_cdc`, `identity_sync`. Later options of the same port: Postgres + Debezium and Keycloak behind the same connectors (S17). `config/default.yaml` is unchanged (`connect: webhook_with_polling`, "not built yet"): choosing connectors per source in config, and running them as a service, comes with the pipeline wiring in S12.

## 6. What the tests prove
- **Contract v2:** a delete with a hash, an ACL or a person is refused; a delete may say who deleted.
- **People:** the synthetic company maps 12/12 with no gaps; an unmapped hire is reported, logged and listed as a gap; a joiner is picked up by the reload; unknown users are dropped from an ACL and reported; an ambiguous name grants no individual access.
- **HR API:** the first poll loads 12 employees and 5 teams, employees mapped, teams about no one, originals canonical; the next poll returns only the changed record with its writer; a delete is invisible to the delta; a change at the boundary repeats with the same version and hash; an unmapped hire is emitted fail closed; a failed poll keeps the cursor; the listing finds the delete (no writer, the time it noticed) and nothing else.
- **Database:** the snapshot gives 16 upserts in commit order with the right ACLs; an update carries its writer, a delete carries nothing; the cursor resumes; an unmapped employee's row is `hr-team` only and both unknown IDs are reported; the reader can't write; unknown tables and ops are errors.
- **Identity:** the first sync lists 12 users; a group removal and a leaver are upserts with the admin as `changed_by`; a change during the listing is replayed; an unknown event kind and a vanished user stop the sync.
- **Mocks:** each records its writer, refuses unknown ones, and changes nothing when it refuses.
- **Docs connectors:** the writer becomes `changed_by`; a user the identity provider no longer knows is dropped from the ACL and reported.
- **Contract:** all six options satisfy `Connector`. **Demo:** runs in-process and from the CLI.

## 7. Run it yourself
```powershell
uv run ekb demo t1 --all
```
You should see: 12/12 people mapped; the initial load of each source; one change in each (dana edits a page, lucia changes Javier's manager, dana records leave for E-1001, bob removes alice from `it-employees`); the four events in the same columns; the leave row's event in full; and lucia's delete of E-1012, silent in the delta and caught by the listing.

By hand, start a mock and pass its URL (`--url`, `--hr-url`, `--idp-url`), then make changes in its `/docs` page with an `X-Actor` header. Try a hire in the HR API with a name no identity user has, and watch it come back as unmapped.

## 8. Decisions and limits
- **Name matching** is the POC's 1-1 link (S1b); production matches on work email or an employee number published by the identity provider.
- **Fail closed, report loudly:** an unmapped person is never guessed. The event is still emitted, with less access, so the change isn't lost.
- **A delete never names whom the item was about** (`person_id` is empty, like the content). It may name who deleted it.
- **Identity records are readable by `all-staff`** (company-directory data). Groups are not tracked as items of their own; membership lives on the user.
- **Cursors live in memory** (`as_of`, `seq`). Keeping them durably, so a restarted connector resumes, comes with the durable stores (S17; decided in S3, since an in-memory change log doesn't survive a restart either).
- **`changed_by` for the database relies on the application** filling the column; Postgres logical replication alone can't tell.
- **The HR listing has no paging** and the person map reloads both sources on a miss: fine for 12 people, batched and rate-limited at scale.

## 9. Check your understanding
1. HR hires Sara Neri (E-1013) before IT creates her identity account. What does the HR connector emit, and how would you notice?
2. Why does the database connector need no full listing, when the HR API connector does?
3. bob removes alice from `it-employees`. Which item gets an event, and why doesn't every Italian policy page get one?

<details>
<summary>Answers</summary>

1. An `upsert` for `hr_api:employees/E-1013` with `person_id` empty (fail closed) and `changed_by` set to whoever hired her, if known. The person map reloads once, still finds no "Sara Neri", adds `employee:E-1013` to `unmapped`, logs a warning, and `gaps()` lists her. Once her identity account exists, the next lookup maps her.
2. CDC reads the database's own log, which records deletes (`op = d`) in commit order, so nothing can disappear unseen. The HR API keeps no trace of a delete: only comparing a full listing with what is known reveals it.
3. `identity:users/alice`: an upsert of her user record, whose groups no longer include `it-employees`, with `changed_by = bob`. The connector doesn't know which pages a group can read, and the pages themselves didn't change. Permission resolution (T8, S11) combines page ACLs with current group membership to work out what she lost.
</details>
