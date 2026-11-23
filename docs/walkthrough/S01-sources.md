# S01 · Sources: the mock HR docs service

> Architecture: `docs/architecture.md` (box 1, "Mock docs service"). Plan: `docs/plan.md` §4.2, S1. Option card: none (the mock is not a pipeline step). Related decisions: D3 (connectors behind one change-event contract), D8 (the source is the authority on permissions), D13 (mocked sources locally).

## 1. In one paragraph
Before the knowledge base can keep itself up to date, something has to change. S1 builds the thing that changes: a small stand-in for SharePoint or Confluence that holds 12 synthetic HR policies, each with a list of who may read it, and records every create, edit, permission change and delete in a **change feed**. It is deliberately *not* part of the product. The product will only ever see it over HTTP, the way a connector sees a real docs tool, and the mock speaks its own dialect (`page.updated`, `rev`, `acl_rev`) so that the next step, T1, has real normalizing to do.

## 2. Where it sits in the flow
```
[ S1 · mock docs source ]  →  S2 · connector + T1 normalize  →  change log  →  T2 …
output: pages (GET /pages, /pages/{id})          input for S2: the feed + the pages
        change feed (GET /changes?since=<seq>)
```

## 3. What was built
| File | What it contains |
|---|---|
| `sources/mock_docs/models.py` | The mock's own shapes: `Page`, `PageSummary` (listing, no body), `PageAcl`, `SourceChange` (one feed entry), request bodies |
| `sources/common/directory.py` | Test users and groups; rejects permissions that name anyone unknown (shared with the identity provider since S1c) |
| `sources/mock_docs/seed.py` | Reads the Markdown + YAML front matter seed files |
| `sources/mock_docs/store.py` | The state: pages + append-only change feed, and the rules for what a change is |
| `sources/mock_docs/app.py` | The HTTP API (FastAPI) |
| `sources/mock_docs/__main__.py` | `python -m sources.mock_docs --port 8081` |
| `sources/synthetic_data/directory.yaml` | 12 test users and 6 groups (extended in S1b–S1d; see its README) |
| `sources/synthetic_data/hr_docs/*.md` | The 12 HR policies (7 en, 3 it, 2 es) |
| `src/ekb/app/demos/s1.py` | `ekb demo s1`: starts the mock as its own process and drives it over HTTP (process helpers in `demos/_mocks.py`, shared by every demo) |
| `pyproject.toml` | `httpx` (product); `fastapi`, `uvicorn` (dev group only, never in the image); a new import-linter contract: **`ekb` must not import `sources`** |
| `tests/sources/mock_docs/` | Seed, store and API tests |
| `tests/unit/app/test_demos.py`, `test_mocks.py` | The demo in-process and through the CLI; starting and stopping mock processes |

## 4. How it works

**The data.** Each document is a Markdown file whose front matter says who may read it:
```yaml
---
id: HR-CMP-01
title: Salary bands 2026 (restricted)
language: en
department: people
acl: { groups: [hr-compensation] }
---
```
Two documents have special jobs. `HR-CMP-01` is readable only by `hr-compensation` (only **bob** is in it; **dana** is HR but not compensation, which makes her the interesting negative case later). `HR-PL-99` is a superseded travel policy that will be deleted in a later step. It deliberately contradicts its replacement `HR-PL-06` (business class over 6 hours vs premium economy over 8), so an answer built from deleted content is easy to spot.

**What "a change" is.** These rules in `store.py` are what S2–S4 are built on:

| Someone… | `rev` | `acl_rev` | `content_hash` | Feed entry |
|---|---|---|---|---|
| creates a page | 1 | 1 | new | `page.created` |
| saves new text | +1 | same | new | `page.updated` |
| saves without changing anything | +1 | same | **same** | `page.updated` |
| changes who may read it | same | +1 | same | `page.permissions_changed` |
| deletes it | gone | gone | gone | `page.deleted` (tombstone) |

The key idea is that **the source never judges whether something really changed**: every save is reported. Real tools behave the same way, which is why T2 (S4) compares hashes and skips the no-op save, and why a permission-only change can take a fast lane that doesn't re-read or re-embed the text.
```python
def edit(self, page_id: str, request: PageEdit) -> Page:
    """Save new content. Always a new ``rev``, even if the text is identical."""
    ...
    page = current.model_copy(update={
        "rev": current.rev + 1,
        "content_hash": content_hash(title, request.body),
        ...
```

**The change feed.** A poll reads its entries and resume cursor under the same lock, so a concurrent deletion remains visible on the next poll. Every write appends a `SourceChange` with an increasing `seq`. A reader that remembers the last `seq` it saw catches up with `GET /changes?since=<seq>`. An entry says *that* a page changed, not what it now says, like a real webhook. The connector fetches the page when it needs the content.

**Permissions are checked against the directory.** `Directory.check` refuses an ACL that names an unknown user or group (`422` over HTTP). Without that, a typo such as `all-staf` would quietly make a policy readable by nobody, a bug you would only notice when answers went missing.

**How the demo reaches the mock without importing it.** `AGENTS.md` says the product never imports `sources/`, and S1 adds an import-linter contract that fails the build if it does. So `ekb demo s1` starts `python -m sources.mock_docs` as a separate process on a free 127.0.0.1 port, waits for `/health`, runs the scenario over HTTP and stops the process:
```python
with mock_client(MODULE, url) as client:  # starts the mock unless --url points at one
    run_s1(client)
```
`run_s1` only needs an `httpx.Client`. The tests pass FastAPI's `TestClient`, which *is* an `httpx.Client`, so they run the same story in-process.

## 5. Options
None. The mock is not a pipeline step, so it has no port, no registry entry and no line in `config/*.yaml`. What later steps add to it:
- **S2:** pushes webhooks to a subscriber (the connector's receiver) on every change. The feed already holds the payloads.
- **S14:** a "may this user read this page right now?" endpoint for the Q4 re-check (D8).
- **S1b–S1d:** the other source types (HR record API, identity provider, HR database), with shared plumbing moved to `sources/common/` (the app factory, change feed, seed loaders and directory this mock now uses).

## 6. What the tests prove
- **Seed:** 12 documents, 7 en / 3 it / 2 es; exactly one restricted document (`HR-CMP-01`, `hr-compensation` only); only bob can read it; `HR-PL-99` exists and is named by its replacement; every permission names a known user or group.
- **Malformed seeds fail loudly:** missing or unclosed front matter, a non-mapping, a bad language code, an unknown field, an empty folder, an unknown group.
- **Edits:** new text gives +1 `rev` and a new hash; identical text gives +1 `rev` and the **same** hash; a title change alone changes the hash.
- **Permissions:** +1 `acl_rev`, `rev` and text untouched; names are sorted and de-duplicated; an unknown name is rejected **and nothing is recorded**.
- **Deletes:** the page disappears from get and listing; the feed has one tombstone; a deleted page can't be edited, re-shared or deleted again.
- **Feed:** ordered by `seq`, timestamps increasing, resumable from any cursor, empty at the head.
- **API:** 404 for a missing page, 409 for a duplicate ID, 422 for an unknown user, a wrong field or a negative cursor; `/reset` restores the seed.
- **Demo:** runs end to end in-process and through the CLI as a real process; repeatable against the same mock; clean errors when the mock is unreachable, exits early or never becomes healthy.
- **Layer rule:** `uv run lint-imports` fails if any `ekb` module imports `sources` (checked by adding a temporary import).

## 7. Run it yourself
```powershell
uv sync --locked          # picks up httpx, fastapi, uvicorn
uv run ekb demo s1
```
You should see the 12 documents with their readers, then an edit of `HR-PL-04` (`rev 1 -> 2`, new hash), a save with no changes (`rev 2 -> 3`, same hash), a permission change on `HR-PL-05` (`acl_rev 1 -> 2`, `rev` unchanged) and the three feed entries.

To play with it by hand, start the mock and use its interactive API page:
```powershell
uv run python -m sources.mock_docs        # then open http://127.0.0.1:8081/docs
uv run ekb demo s1 --url http://127.0.0.1:8081   # the demo against your running mock
```
Try deleting `HR-PL-99`, then `GET /changes?since=12`, and see the `page.deleted` tombstone.

## 8. Decisions and limits
- **Everything is in memory.** A restart (or `POST /reset`) restores the seed. Real tools keep history; we don't need it until replay (S3), and replay in this design comes from our own raw store, not from the source.
- **Hard deletes.** Real tools usually have a recycle bin; the mock behaves like one that has been emptied, the case that matters for the deletion gate.
- **One flat ACL per page.** No inheritance from folders or sites, no "deny" entries. Production connectors must flatten inherited permissions into users and groups (D3, D8); the mock starts already flat.
- **Webhooks are not pushed yet.** The feed holds what they would carry; delivery comes with the connector's receiver in S2.
- **No authentication on the mock.** It binds to 127.0.0.1 only and holds synthetic data.
- **The demo needs to run from the repository root**, because the mocks are plain folders, not installed packages. Otherwise it says so and suggests `--url`.
- **`fastapi`/`uvicorn` are dev-only dependencies**, so `uv sync --no-dev` (the image) does not install the mock. The product only gained `httpx`, which the connector needs in S2.
- **Known warning:** Starlette's test client warns that it will move from `httpx` to `httpx2`. Harmless today; revisit when Dependabot bumps Starlette.

## 9. Check your understanding
1. An editor opens `HR-PL-04` and clicks Save without changing anything. What does the mock report, and which later step decides that no work is needed? How will it tell?
2. Why does a permission change raise `acl_rev` instead of `rev`, and what does that let the pipeline skip?
3. `ekb demo s1` needs the mock, yet `ekb` may not import `sources`. How does the demo reach it, and what stops someone from "just importing it" in a later step?

<details>
<summary>Answers</summary>

1. A `page.updated` entry with `rev` one higher and the **same** `content_hash`. The source reports every save; it is T2 (the diff step, S4) that compares the new hash with the one in the document registry and skips re-processing when they match.
2. Because the text didn't change, only who may read it. Keeping the two counters apart lets T2 route the event to the permission fast lane: update the ACL on the existing chunks without re-parsing, re-chunking or re-embedding (and without paying for model calls). It also lets permission changes propagate faster than content (the < 5 min target in `decisions.md` §0).
3. It starts the mock as a separate process (`python -m sources.mock_docs` on a free local port) and talks to it over HTTP, the same way a connector talks to a real docs tool. The import-linter contract "The product never imports the mock sources" in `pyproject.toml` fails `uv run lint-imports`, and therefore CI, if any `ekb` module imports `sources`.
</details>
