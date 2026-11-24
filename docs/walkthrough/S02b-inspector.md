# S02b · Inspector UI foundation

> Architecture: `docs/architecture.md` (box 8, Operate). Plan: `docs/plan.md` §4.2, S2b. Decision: D19 (inspector UI: React + TypeScript, local only, typed contract). Option card: none.

## 1. In one paragraph
Tests prove each step works; the inspector lets you **see** it. S2b builds the foundation every later step adds a panel to: a read-only inspector API in Python and a React + TypeScript app. For now it shows what the connectors have produced: a **Documents** screen (every tracked item, its current state and its full history) and a live **Events** panel. Two rules shape it. It shows everything, every document and every permission, so it is **local only** by construction. And the front end and back end share **one contract**: the UI's TypeScript types are generated from the API's OpenAPI schema, and CI fails if they drift apart.

## 2. Where it sits in the flow
```
4 mock sources ─▶ 6 connectors (S2, S2a) ─▶ EventLog (in memory; the change log from S3)
                                                 │
                         inspector API (FastAPI, 127.0.0.1:8090, GET only, local profile only)
                                                 │  /api/*   (OpenAPI → ui/openapi.json → ui/src/api/schema.d.ts)
                         ui/ (Vite dev server, 127.0.0.1:5173, proxies /api)  ─▶  your browser
```

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/operate/inspector/log.py` | `EventLog`: every event in arrival order; per-item latest state; `known(source)` for the full listings |
| `src/ekb/operate/inspector/models.py` | The API's view models (`EventView`, `DocumentView`, `SourceView`, `EventPage`): the UI contract |
| `src/ekb/operate/inspector/app.py` | The inspector API: `/api/health`, `/api/sources`, `/api/documents`, `/api/events`; the local-only gate; `openapi_json()` |
| `src/ekb/app/inspector.py` | `ekb inspector`: starts the mocks, wires the six connectors to the log, syncs in rounds (`SyncLoop`), serves the API; `--export-openapi` |
| `ui/` | Vite + React 19 + TypeScript 5.9 app: `App.tsx`, `components/DocumentsScreen.tsx`, `EventsPanel.tsx`, `EventTable.tsx`, `api/client.ts`, generated `api/schema.d.ts`, `openapi.json`, plain CSS |
| `.github/workflows/ci.yml` | New `ui` job: types current, eslint, `tsc`, build |
| `Makefile` | `ui-types` (regenerate the types), `ui-check` (what CI runs for the UI) |
| `tests/unit/operate/inspector/`, `tests/unit/app/test_inspector_command.py` | Endpoints, gates, contract freshness, the sync loop, the command |

## 4. How it works

**Where the events come from.** There is no change log yet (S3), so `ekb inspector` builds a small live system in one process: it starts the four mocks, wires all six connectors to an in-memory `EventLog`, and runs them in rounds. Every round runs the frequent connectors (the webhook queue, the HR delta, CDC, identity sync); every `--listing-every` rounds (10 by default) it also runs the two full listings, which catch deletes. Round 0 loads every source. Both the polling interval and the number of rounds between listings must be positive. A failing connector is logged and retried next round; it never stops the others. The log also answers "what does the knowledge base believe each source holds" (`known`), which the listings compare against, the job the document registry takes over in S3.

**The API is read-only and local-only.** Every route is a `GET` (a test fails if a write route appears). The factory refuses any profile but `local`:
```python
if profile != LOCAL_PROFILE:
    raise InspectorNotAllowedError(...)
```
and the command serves on `127.0.0.1` only. Agents never use it; they go through the MCP access layer (S15).

**Live polling with a cursor.** `GET /api/events?since=<n>` returns the events after `n` and the new cursor. The Events panel prepends each response and waits two seconds before starting the next request, the same pattern as the docs tool's change feed (S1).

**One contract, generated.** The view models in `models.py` are the API's schema. `ekb inspector --export-openapi ui/openapi.json` writes it, byte for byte stable, and `npm run gen:types` turns it into `ui/src/api/schema.d.ts`. The UI imports only those types:
```ts
type Schemas = components["schemas"];
export type DocumentView = Schemas["DocumentView"];
```
So a renamed field in Python is a TypeScript compile error, not a blank cell at run time. Two checks keep it honest: a Python test compares `ui/openapi.json` with the live schema, and CI regenerates both files and fails on any difference.

**The screens.** *Documents*: every item with its last op, version, `person_id`, `changed_by`, readers and event count; deleted items greyed with their name struck through; click one for its history, newest first. *Events*: every event as it arrives, filterable by source, with pause. The source chips on top count items, deletes and events per source.

## 5. Options
Not a pipeline step, so no options. Later steps each add a panel (S3 onwards: change log, T2 decisions, parsed text, chunks …), and two larger screens: **Search as user** (S13) and **Lifecycle** (S16). From S3 the `EventLog` is replaced by a reader over the change log.

## 6. What the tests prove
- **Log:** knows each source's live items (deletes excluded) and its cursor.
- **Documents:** each item's latest state, event count, ACL as lists, deleted flag; filter by source.
- **Events:** paging after a cursor; filtering by item gives that item's history; new events appear after the cursor.
- **Sources:** items, deletes and events counted per source.
- **Gates:** refuses the `ci`, `bedrock`, `production` and empty profiles; every route is `GET`; host is `127.0.0.1`; the command refuses under the `ci` profile.
- **Contract:** `ui/openapi.json` matches the API exactly; `--export-openapi` writes it.
- **Sync loop:** round 0 loads 12 pages, 17 HR records, 16 rows and 12 users; nothing changed gives nothing; a later round's listing catches an HR delete; a failing source (no database file) doesn't stop the others; the loop runs in the background.
- **UI (CI):** eslint, `tsc` strict, production build, types current.

## 7. Run it yourself
Two terminals, from the repository root:
```powershell
uv run ekb inspector          # local profile is the default
```
```powershell
cd ui
npm install                   # once
npm run dev                   # then open http://127.0.0.1:5173
```
The first terminal prints each mock's `/docs` URL. Make a change there, with an `X-Actor` header to say who, and watch it appear in the Events panel within a few seconds: edit a page (webhook), patch an HR record (delta), remove someone from a group (identity sync). Delete an HR record: the next CDC round adjusts its leave-row readers, and the HR record deletion appears after the next full listing (up to `--listing-every` rounds; try `--listing-every 2`). Click an item on the Documents screen to see its history.

## 8. Decisions and limits
- **Local only, read only, 127.0.0.1**, enforced in code and tested (D19). No authentication: it is never exposed.
- **Events live in memory** and are lost on restart; the change log (S3) makes them durable.
- **The serving part of `ekb inspector` is smoke-tested by hand**, not by unit tests (it starts processes and a server); its pieces (`build_connectors`, `SyncLoop`, the API) are tested.
- **TypeScript 5.9, not 7**: `typescript-eslint` and `openapi-typescript` don't support TypeScript 7 yet.
- **No UI unit tests**, as agreed: lint, strict types and the build. Tests in Python remain the proof.
- **Polling, not push**: simple and enough for a local tool; server-sent events would be the next step if it ever felt slow.
- **Plain CSS, no component library.**

## 9. Check your understanding
1. Why does renaming `changed_by` in `models.py` break the UI's build instead of the page at run time, and which two checks catch a forgotten regeneration?
2. You delete an HR record while `ekb inspector` runs. Why does it take longer to appear than a page edit?
3. What stops the inspector from being started in CI or in production, and why does that matter more for this API than for the Knowledge API?

<details>
<summary>Answers</summary>

1. The UI's types come only from `schema.d.ts`, generated from the API's OpenAPI schema, so code using the old name no longer compiles (`tsc`). A forgotten regeneration is caught by the Python test that compares `ui/openapi.json` with the live schema, and by the CI `ui` job, which regenerates both files and fails on any diff.
2. A page edit arrives by webhook and is turned into an event in the next round. The HR API keeps no trace of a delete, so only the full listing finds it, and the listings run every `--listing-every` rounds, not every round.
3. `create_inspector_app` refuses any profile but `local` (and the command exits with an error), it binds to 127.0.0.1 only, and it has no write routes; tests check all three. The inspector deliberately shows every document and permission without checking who asks, while the Knowledge API filters everything by the asking user's permissions.
</details>
