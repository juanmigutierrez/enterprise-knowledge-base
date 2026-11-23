# S01b · Sources: the mock HR record API

> Architecture: `docs/architecture.md` (box 1, "Mock CRM / HR API"). Plan: `docs/plan.md` §4.2, S1b. Option card: none (the mock is not a pipeline step). Related decisions: D3 (delta APIs and the nightly full listing that catches deletes), D7 (structured data).

## 1. In one paragraph
Most record systems (Workday, Salesforce) don't push anything. You have to **ask** them what changed, usually with "everything updated since time X". And when a record is deleted, they don't tell you at all: it just stops appearing. S1b builds a stand-in with exactly those two habits: 12 employees and 5 teams over REST, with an `updated_since` filter and **silent deletes**. It is the reason the connector (S2a) has to run a full listing on a schedule, and the reason a deletion gate needs more than webhooks.

## 2. Where it sits in the flow
```
[ S1b · mock HR record API ]  →  S2a · delta polling + full listing (T1)  →  change log  →  T2 …
output: GET /employees, /teams  (records + as_of)
        GET /employees?updated_since=<as_of>   (only what changed; never deletes)
```

## 3. What was built
| File | What it contains |
|---|---|
| `sources/mock_hr_api/models.py` | `Employee`, `Team`, partial-update bodies, `RecordList` (records + `as_of`) |
| `sources/mock_hr_api/store.py` | The records and the rules: inclusive deltas, silent deletes, referential checks |
| `sources/mock_hr_api/app.py` | The HTTP API (FastAPI), built with the shared factory |
| `sources/mock_hr_api/__main__.py` | `python -m sources.mock_hr_api --port 8082` |
| `sources/synthetic_data/hr_api.yaml` | 12 employees (Milan 6, Madrid 3, Toronto 3) in 5 teams |
| `sources/common/` | Shared by every mock: app factory, change feed, seed loaders, clock, directory (the full list is in §3 of S01d) |
| `src/ekb/app/demos/s1b.py` | `ekb demo s1b` |
| `tests/sources/mock_hr_api/` | Store and API tests |
| `tests/sources/test_cross_source.py` | The 1-1 name link with the identity provider; headcount agreement with the database |

## 4. How it works

**Different IDs, linked by name.** The HR API has its own IDs (`E-1001`, `T-ENG`); the identity provider (S1c) calls the same person `alice`. As decided for this step, the two are linked **1-1 by full name**: every identity user has exactly one employee with the same `name`, and the reverse. That only works if names are unique, so the store refuses a hire or rename that would duplicate a name:
```python
raise RecordConflictError(
    f"name {employee.name!r} is already used by {clash[0]}; names link to the "
    "identity provider 1-1, so they must be unique"
)
```

**Pulling changes.** Every listing returns `as_of`, the server time when it was built. A reader passes it back next time: `GET /employees?updated_since=<as_of>` returns only records stamped at or after that time.
```python
selected = records if since is None else [r for r in records if r.updated_at >= _as_utc(since)]
```
The `>=` is the decision from the plan: **inclusive**. A record stamped at exactly the `as_of` instant is returned, possibly twice, but it can never fall into the gap between two polls. With `>` it could be skipped for ever. A repeat costs nothing, because T2 (S4) compares content and drops it.

**Every save stamps the record**, even if nothing changed, as in S1: the source never judges.

**Deletes leave no trace.** `delete_employee` removes the record and records nothing. No delta will ever mention it. The only way to notice is to compare two full listings:
```
first listing  {E-1001 … E-1012}
new listing    {E-1001 … E-1011}
missing        {E-1012}   ← this is the delete
```

**Who may read a record?** Record systems usually have no per-record permission list. As decided for this step, the records hold directory fields only (name, work email, team, office, manager, start date; **no pay data**), and the connector will apply one rule: readable by `all-staff`.

**Integrity rules.** Like a real HR system, the mock refuses a team or manager that doesn't exist, someone managing themselves, and deleting someone who still manages people or leads a team (409). That keeps the data consistent for the steps that link documents to people (S9).

## 5. Options
None: the mock is not a pipeline step. What later steps add: the S2a connector reads it two ways (delta polling for edits and hires, a scheduled full listing for deletes); S9 links documents to these people and teams.

## 6. What the tests prove
- **Seed:** 12 employees, 5 teams, 6/3/3 per office; names unique; sorted listing.
- **Deltas:** only changed records come back; nothing new means an empty delta; teams work the same way.
- **Inclusive boundary:** a change stamped exactly at `updated_since` is returned; one microsecond later it isn't. A change at the `as_of` instant is never missed, may repeat, and stops repeating once the cursor moves on.
- **A no-op save still stamps the record.** A time without a zone is read as UTC.
- **Deletes:** absent from every delta, from the listing and from `GET` (404); only comparing listings shows them; deleting again is 404.
- **Refused changes leave nothing behind:** duplicate ID, duplicate name, unknown team or manager, self-manager, deleting a manager or team lead.
- **Partial updates:** an omitted field is kept; explicit `null` clears the manager; `null` for a required field is ignored.
- **Seed errors:** a missing team lead or a duplicate employee ID fails loudly.
- **Cross-source:** identity users and HR records match 1-1 by name and agree on office; the database's headcount and leave rows match these records.

## 7. Run it yourself
```powershell
uv run ekb demo s1b
```
You should see the 12 employees, then a move (the delta returns only `E-1008`), a delete (the delta returns nothing; comparing listings shows `E-1012` missing), and a refused delete of a team lead (409).

By hand:
```powershell
uv run python -m sources.mock_hr_api      # then open http://127.0.0.1:8082/docs
uv run ekb demo s1b --url http://127.0.0.1:8082
```
Try: `GET /employees` (copy `as_of`), `DELETE /employees/E-1011`, then `GET /employees?updated_since=<as_of>`. The record is gone and the delta is silent.

## 8. Decisions and limits
- **Name matching is fragile in real life** (two people can share a name). It was chosen for the POC and is enforced as a rule; production would match on work email or an employee number from the identity provider.
- **Timestamps, not a sequence number.** That is how most delta APIs work, and why the boundary is inclusive. Clock skew between servers is not modelled.
- **Hard deletes.** Many HR systems mark leavers as terminated instead; the identity provider (S1c) shows that pattern, this mock shows the harder one.
- **In memory, no paging, no authentication**, 127.0.0.1 only, synthetic data.

## 9. Check your understanding
1. HR deletes Olivia Chen's record. Which request would tell the connector, and why doesn't the delta?
2. Why is `updated_since` inclusive (`>=`), and what is the price?
3. The HR API says `E-1001`, the identity provider says `alice`. How do later steps know it is the same person, and what does the mock do to keep that link reliable?

<details>
<summary>Answers</summary>

1. Only a full listing compared with the previous one (or with the document registry): `E-1012` is in the old set and not in the new one. The delta returns records stamped since a time; a deleted record has no stamp to return, and the API keeps no tombstone.
2. So a change stamped at exactly the cursor instant is never lost between two polls. The price is an occasional duplicate, which is harmless because T2 compares content hashes and skips it.
3. By full name, 1-1 (the decision for this step): every identity user has exactly one HR record with the same name. The mock refuses any hire or rename that would duplicate a name, and `tests/sources/test_cross_source.py` checks the 1-1 match on the seed data.
</details>
