# S16 · Self-maintenance: changes in the sources reach the answers with no manual step

> Architecture: `docs/architecture.md` (the write path end to end, box 4's worker, box 5's stores, and the "current access check" arrow from the API back to the sources). Plan: `docs/plan.md` §4.2, S16. Option cards: `docs/option-evaluation.md` 4.13 (permission enforcement: the acceptance test, cached path included) and 4.14 (change propagation: scripted add, edit, delete and revoke; the publication test). Decisions: D3 (the nightly full listing catches deletes; one canonical person ID), D6 (entity IDs on the chunks), D8 (the second check before release), D16 #10 (verify a cached answer on reuse), D19 (the inspector, amended here).

## 1. In one paragraph
The assignment asks for a knowledge base that **maintains itself**. Edits, deletions and permission changes in the sources should reach every answer without anyone touching the knowledge base. Steps S2 to S15 built the parts. S16 proves the whole with **one scripted story of eight changes**, each made in a source by the person who owns it:
- a page is added, edited, revoked on the page and revoked by removing someone from a group;
- a person is hired in HR, and their identity account follows;
- a person leaves (only the HR full listing can see that);
- a page is deleted.

The same script drives `ekb demo lifecycle`, the inspector's new **Lifecycle** screen, and the **gate tests** in `tests/gates/`. Three things that were missing are added:
- **re-linking:** when a person or team changes, the worker processes again the pages that name them (left open in S9);
- **a hire whose identity account comes after the HR record** now becomes an entity;
- **a purge empties the caches too:** answers that cited a deleted page, and model replies that quoted it (left open in S15).

## 2. Where it sits in the flow
```
a person changes a source            (docs tool · HR record API · identity provider)
   │  webhook · delta · identity sync · full listing        (S2, S2a)
   ▼
change log ─▶ worker: T2 → T8 for each event                (S3 … S12)
   │             └─ the entity store changed? note it ──┐
   │                                                     ▼
   │          after the drain: Pipeline.relink() ─▶ pages that carry or name the entity,
   │                                                 whose links are now stale
   │                                                 ─▶ T3 … T8 again, from the stored original
   │          a purge ─▶ search, readers, entity, model replies that quoted the page,
   │                     cached answers that touched it
   ▼
stores ─▶ Q1 … Q4 ─▶ ask / search over MCP                  (S13 … S15)
            └─ Q4 asks the source now: a revocation or a delete holds before the worker runs

input:  one scripted change, e.g. ACTIONS["leaver"]: bob deletes E-1009 in the HR API
output: what the agent is told before and after, and what the worker did in between
        (in the demo, the Lifecycle screen and tests/gates/)
```

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/app/lifecycle.py` | The script: `ACTIONS` (eight `Action`s, each with who, which source, the change, which connector catches it, whether only a full listing can, what should happen, and `apply(Sources)`), `PROBES` (who asks what), `check` (the story's order), `Sources` |
| `src/ekb/process/t6_entities/relink.py` | `EntityChange` (before and after, `matters`, `describe` by ID only), `candidates` (pages that carry or name a changed entity), `stale` (would `exact_match` link a page differently now?) |
| `src/ekb/process/t6_entities/match.py`, `options/exact_match.py` | `find_all`: how a chunk's passages are read, now shared by the linker and the re-link check |
| `src/ekb/app/relinks.py` | `Relinks`: entity changes not looked at yet, and the pages queued (with their reasons) |
| `src/ekb/app/pipeline.py` | The worker notes every entity change (record events and purges). `Pipeline.relink(retry_headers=)` re-processes stale pages; `_reprocess` is the rebuild path S12 had, shared |
| `src/ekb/contracts/trace.py` | `TraceStatus.RELINKED`; the `re-link` step |
| `src/ekb/process/t4_chunk/header.py`, `options/header_model.py` | `NO_HEADER`, `header_failed`: how a failed header is recognized |
| `src/ekb/connect/people.py`, `connect/options/hr_api_full_listing.py` | `PersonMap.maps_now`, `settle`; the HR full listing sends an employee again once they map to a person |
| `src/ekb/platform/gateway/port.py`, `gateway.py`, `options/cache_memory.py`, `options/cache_sqlite.py` | The **quotes** relation: `put(…, quotes=)`, `quote`, `complete(…, quotes=)`; `forget` drops every reply that quotes the deleted page. SQLite gains a `quotes` table, created in an existing file |
| `src/ekb/access/answer/options/write_model.py` | The answer's model reply records the pages it quotes |
| `src/ekb/access/answer/port.py`, `options/cache_memory.py` | `AnswerCache.forget(document_key)`, `CachedAnswer.documents()` |
| `src/ekb/app/system.py` | `KnowledgeSystem`: re-links after each drain, forgets cached answers on a purge (`forgotten_answers`), keeps the last 1,000 traces |
| `src/ekb/app/demos/lifecycle.py`, `demos/__init__.py` | `ekb demo lifecycle` |
| `src/ekb/operate/inspector/lifecycle.py`, `models.py`, `app.py` | `LifecyclePanel` (apply one change with the loop held, the answers before, right after and now); `GET /api/lifecycle`, `POST /api/lifecycle/actions/{action_id}` |
| `src/ekb/app/inspector.py` | `SyncLoop.paused`, `listing_next_round`, re-links after each drain; `LateActions`, `lifecycle_panel` |
| `ui/src/components/LifecycleScreen.tsx`, `ui/src/lifecycle.ts`, `App.tsx`, `api/client.ts`, `pipeline.ts`, `styles.css`; `ui/openapi.json`, `src/api/schema.d.ts` | The **Lifecycle** tab, the `relinked` status, the regenerated contract |
| `tests/gates/` | `story.py` (the whole system on in-process mocks, two profiles), `test_deletion.py`, `test_revocation.py`, `test_publication.py`, `test_leakage.py`, `test_story.py` |
| `tests/unit/process/t6_entities/test_relink.py`, `tests/unit/app/test_relinks.py`, `conftest.py`; additions to `test_demos.py`, `test_inspector_command.py`, `test_hr_api.py`, `test_stores.py`, `test_gateway.py`, `test_cache.py`, `test_writers.py`; `tests/unit/operate/inspector/test_lifecycle_panel.py`; `ui/tests/lifecycle.test.mjs` | See §6 |
| `.github/workflows/ci.yml` | Comments only: the gates run in the `test` job |

## 4. How it works

### The script changes sources, never the knowledge base
Each action is the HTTP call the source's owner would make, with the person in `X-Actor`:
```python
def _leaver(s: Sources) -> None:
    _call(s.hr, "DELETE", f"/employees/{LEAVER_EMPLOYEE}", "bob")
```
Nothing in `ekb.app.lifecycle` touches a store. The connectors notice the change, the worker applies it, and the query path answers from the result. Those are the paths the other steps built, and S16 adds nothing to them for the scenario's sake. `check` keeps the story in order: `edit_page` needs `add_page`, and an action runs once.

| # | Action | Source | Caught by |
|---|---|---|---|
| 1 | `add_page`: HR-PL-11, mentoring, all-staff; names Lucía Navarro and Sara Esposito (not hired yet) | docs tool | webhook |
| 2 | `edit_page`: "2 times a month" becomes 3 | docs tool | webhook |
| 3 | `revoke_page`: HR-PL-11 narrowed to hr-team | docs tool | webhook (T2's fast lane) |
| 4 | `remove_from_group`: lucia out of hr-team | identity provider | identity sync |
| 5 | `hire_record`: Sara (E-1013) in the People team, no account yet | HR record API | HR delta |
| 6 | `hire_account`: Sara's identity account | identity provider | identity sync, then the HR full listing |
| 7 | `leaver`: Lucía's HR record (E-1009) deleted | HR record API | the HR full listing only |
| 8 | `delete_page`: HR-PL-99, the superseded travel policy | docs tool | webhook |

### Re-links: the pages an entity change affects
T6 writes a page's links when the page is processed (S9). A hire, a leaver or a renamed team changes no page, so until S16 a page kept the links it was given. Now the worker notes every change it makes to the entity store, in the record path and in a purge:
```python
record = run.step("entity store", lambda: steps.entities.handle(event), _record_detail)
self.relinks.changed(EntityChange(record.replaced, record.entity))
```
`EntityChange.matters` ignores changes no link can see (a new office or manager). After the drain, `Pipeline.relink()` turns the changes into pages:
- **candidates:** a page is affected if one of its published chunks carries the entity's ID (a leaver), or names it before or after the change (a hire, a rename). A hire who shares a name with someone already linked is a candidate too: the name is now ambiguous, so the old link must go;
- **stale:** a candidate is processed again only if `exact_match` would now link it differently. The check reads chunks exactly as the linker does (`match.find_all` over `port.words`), so the two cannot disagree. Pages linked by another linker (a model tier, later) are always processed again, because their links can't be predicted.

A stale page is processed again **from its stored original**, through T3 to T8, as S12's rebuild does. The trace starts with a `re-link` step naming the change by ID only, and ends as `relinked`:
```
hr_docs:HR-PL-11 v2.2: re-link -> T8 readers first -> T3 -> … -> T8 publish = relinked
  re-link: person:lucia removed
```
The page's version doesn't change, because the page didn't. Model calls are answered from the gateway's cache, because the text is the same.

**Why after the drain, not inside the HR record's event.** One record can affect many pages. If one of them failed inside the record's event, the event would come back, be retried, and be parked after three attempts. The record (a leaver) would then wait behind a page. So each page is processed on its own: one that fails stays queued for the next round, still searchable with its old links. Links are not a permission gate. A link to a leaver names an ID the entity store no longer answers for. A page in the dead letters is left to its retry, which links it against today's entities anyway.

The same queue takes **pages whose context header failed** (S8 promised "a reconciliation run"). On listing rounds, if the header option writes headers at all, a page whose `header_id` ends in `, no header)` is queued and processed again; once a model answers, it gets its headers (`rebuilt`).

### A hire whose account comes later
HR usually hires before IT creates the account. Sara's first HR event therefore has no person (D3: fail closed), and her record defines no entity. The record doesn't change when the account appears, so no version comparison would ever send it again. The HR full listing now asks the person map about every employee it reported as unmapped, and sends the same version again once they map:
```python
if unseen.pop(record.source_id, None) == normalize.version_of(record):
    label = f"employee:{record.id}"
    if (
        record.collection != "employees"
        or label not in reported
        or self._people.maps_now(record.id, record.name) is None
    ):
        continue
    self._people.settle(label)  # sent again below, with its person
```
T2 already knew what to do with it: "same content, now about sara (was no one)" means `process`. The entity store gets `person:sara`, and the re-link adds her to HR-PL-11.

### What a purge empties now
A purge removed the page from search, the readers record, the entity store and the model cache's per-page replies (S12). Two places could still hold a deleted page:
- **The answer cache.** An entry was only checked when it was reused (S15). Now `KnowledgeSystem` watches the worker's outcomes, and a purge drops every cached answer whose hits, source checks or passages touched the page (`AnswerCache.forget`).
- **Model replies that quoted it.** The gateway's tags mean "used by": a context header shared by two identical documents stays while either is alive. That is wrong for an answer, which repeats every page in its prompt. So a reply now also records what it **quotes**, and `forget` drops it as soon as any one of them goes:
  ```python
  reply = self._gateway.complete(self._task, prompt(question, passages), ..., quotes=quoted(passages))
  ```
  The SQLite cache gets a `quotes` table, added to an existing file when it opens. A reply cached before S16 learns its quotes the next time it is reused.

### The gate tests
`tests/gates/story.py` builds the whole POC as `ekb api` does (`KnowledgeSystem`), on in-process mocks, offline. Every gate runs twice, on the `ci` and the `local` profile: the gates must hold whatever the options are. Every reply an agent gets is kept, so a test can scan everything anyone was told. The gates run in the pull-request job (`ci.yml`'s `test`): in memory, offline, about 25 seconds.

### The demo and the Lifecycle screen
`ekb demo lifecycle` serves the MCP tools and the webhook receiver. It issues test tokens for alice, lucia and bob, then plays the eight steps. For each step it shows the change, which connector caught it, what the worker did (traces), and what the agent is told. For the revocations and the delete, it asks **before** the worker runs too, because that is where Q4 must already hold. Each step states its promise and whether it held. If one doesn't, the command exits with 1.

The **Lifecycle** tab shows the same story with one button per change. Each answer appears at three moments:
- **before** the change;
- **right after** it: the API holds the sync loop while it applies the change and asks again, so the worker has not run and the index is stale;
- **now**.

Below each step is what the worker did since. A change only a full listing can see makes the next round run the listings, so you don't wait for the tenth round.

**The one write route (D19, amended).** `POST /api/lifecycle/actions/{action_id}` is the inspector's only route that is not a GET. It runs one of the eight scripted changes, in order and once, against a **mock source**, as its owner would, and writes nothing to a store. The test that kept the inspector read-only now allows exactly this route and no other.

## 5. Options
- **Built now:**
  - re-linking by processing the page again from its stored original (T3 to T8, cached model calls), after each drain;
  - detection by scanning the published chunks with `exact_match`'s rules;
  - the re-link queue in memory.
- **Later:**
  - a link-only lane that rewrites `entity_ids` in place, without T3 to T7 (cheaper at scale);
  - a name query on OpenSearch instead of scanning every chunk (S17);
  - the model tier's own check (card 4.10's cascade). Its pages are always processed again for now;
  - `changes_since` over MCP: S16b, an optional step.
- **Switching:** nothing new in `config/`. The re-link uses whatever linker the profile names; header retries happen only when `t4_header` writes headers.

No default changed.

## 6. What the tests prove
- **Deletion gate (`tests/gates/test_deletion.py`):**
  - a deleted page is never cited again, fresh or cached, by `ask` or `search`, and neither its title, its key nor its unique text appears in any reply after the delete;
  - nothing of it stays: no chunks, readers, originals (a tombstone holds no content), and every cached answer that touched it is dropped at the purge;
  - a model reply that quoted it leaves the model cache, and the next question goes to the model again;
  - a delete still waiting in the change log is held back at once (`deleted`), with the index still holding the page;
  - an HR record deleted without a trace is found by the full listing only, and the page loses the link without an edit;
  - a page recreated under the same ID brings nothing of the deleted version back.
- **Revocation gate (`test_revocation.py`, card 4.13):**
  - with alice's answer cached, the page is narrowed in the source and the index left stale. The cached path, the fresh path and `search` all leave it out, without its title or text. Lucía still gets it, cited;
  - the source times out: nothing is released, no cached answer is served, not even to lucia, and nothing incomplete is kept;
  - after the sync, the fast lane copies the readers and no vector changes;
  - removing lucia from hr-team: before the identity sync, Q4 holds the page back (`denied`). After it, no chunk was rewritten, `whoami` lists no hr-team, and bob still gets the page.
- **Publication gate (`test_publication.py`, card 4.14):** with T8 paused after T7, the embedded page is invisible to everyone. After T8 publishes, only its readers find it.
- **Leakage gate (`test_leakage.py`):** at every step of the story, seven people ask every question, including one that claims to be bob, through `ask` and `search`. The salary bands reach no one but bob.
- **The story (`test_story.py`):** each step does what it promises. The page is re-linked twice (the hire, the leaver) and never edited for it. The script keeps its order.
- **Re-links (`test_relink.py`, `test_relinks.py`):**
  - a hire, a leaver, a team rename, a namesake hire (ambiguous: linked to neither);
  - a change no link sees is ignored;
  - the initial load needs no re-link;
  - a page in the dead letters is left to its retry;
  - a failed re-link stays queued and the page stays searchable;
  - a deleted page leaves the queue;
  - failed headers are retried on listing rounds only, and never when headers are off on purpose;
  - a hire whose account comes after the HR record is linked after the listing.
- **Caches:** both caches drop a reply quoting a deleted page while keeping replies other documents use. Quotes can be added to an old reply, are counted once, and survive a restart. On first opening a pre-S16 SQLite file, the migration preserves tagged replies and discards untagged replies whose sources cannot be recovered. Later opens preserve new independent entries. The answer cache forgets every answer that touched a page.
- **The HR listing:** a hire is sent again once their account exists, once only, with the same version and hash and now a person.
- **Lifecycle screen:**
  - the panel's order, the three moments, the loop held while a change is applied, and a page held back once per answer;
  - 404, 409, 502 and 503;
  - newest first, each step's traces up to the next change;
  - against the real mocks: a revocation holds "right after", a listing action makes the next round list, a hire is re-linked;
  - the inspector allows exactly one write route;
  - the UI helpers label answers, changes, routes and actions.
- **Demo:** the whole story offline, all 16 promises held, tokens masked, no warnings, traces naming IDs only; the CLI exits 1 on an unreachable mock, a broken promise, a restricted citation to another user, or a citation to the deleted page after deletion.

## 7. Run it yourself
```powershell
uv run ekb demo lifecycle
```
It needs no model, key or network. You should see:
1. **The options** as in S15, with three demo tokens (alice, lucia, bob), and `the docs webhook is subscribed`.
2. **Add:** `hr_docs:HR-PL-11 v1.1: T2 process -> … -> T8 publish = published`; alice's answer cites HR-PL-11 (`Mentoring groups meet 2 times a month …`); `HR-PL-11 is linked to person:lucia, team:T-PPL`.
3. **Edit:** before the worker runs, alice gets the old text marked `(source changed since indexing)`, and the inspector view says her cached answer was dropped. After it runs, she gets `3 times a month`.
4. **Revoke on the page:** before the worker, `HR-PL-11 is not cited` for alice and `Q4 held back HR-PL-11 (denied)`; lucia's cached answer still cites it. Then `T2 acl_only -> T8 fast lane = readers` and `vectors unchanged: True`.
5. **Revoke by group:** lucia loses the page before the identity sync. Then `permission store: lucia: -hr-team`, `whoami for lucia -> groups all-staff, es-employees`, `chunks rewritten: 0`; bob still gets it.
6. **Hire, in HR:** `defines no entity (no identity user for this employee: fail closed)`.
7. **Hire, her account:** `same content, now about sara (was no one)`, `entity store: person:sara added`, then `re-link: person:sara added` and `relinked`.
8. **Leaver:** after the HR delta, `person:lucia still in the entity store: True`; after the full listings, `purge entity: person:lucia removed`, `re-link: person:lucia removed`, `HR-PL-11 is linked to person:sara, team:T-PPL`.
9. **Delete:** before the worker, `Q4 held back HR-PL-99 (deleted)`; after, `4 chunks out of the search store` and `cached answers that touched HR-PL-99 and were dropped: 1`; the answer cites HR-PL-06.
10. **The gates:** leakage 0, deleted content 0, revocations effective before the worker ran, `re-links: 2`, `promises held: 16 of 16`.

`EKB_PROFILE=ci uv run ekb demo lifecycle` tells the same story with the `ci` options. `--models` writes the answers with the gateway's `answer` task.

In the inspector:
1. `uv run ekb inspector --header none --embed fake`, then `npm run dev` in `ui/`, and open **Lifecycle**.
2. Press **1. Add a page**. "Right after" shows HR-PL-11 not cited yet (the worker hadn't run). A second later, **Now** cites it, and the step lists `T2 process → … → T8 publish`.
3. Press **2** and **3**. On step 3, alice's "right after" says `held back (denied)` with `lost`, before any round ran.
4. Press **5**, then **6**: the step shows `relinked`, and the links under **Now** gain `person:sara`. Press **7**: she leaves the links again. Open **Pipeline** to follow HR-PL-11's attempts.

If the source change succeeds and its immediate observation fails, the Lifecycle screen records the change and leaves **Right after** empty. A later poll retries **Now** without repeating the mutation.

## 8. Decisions and limits
- **Settled in S16's plan (26 November 2026):**
  1. The Lifecycle screen applies the scripted changes through `POST /api/lifecycle/actions/{action_id}`, local profile only. The route changes mock sources as a named synthetic person, never a store. D19 is amended to say so. The screen shows Q1 to Q4's cited pages, not the HR assistant's written answer.
  2. When an entity changes, the worker queues the pages that carry its ID or name it, before or after. After each drain it processes them again from their stored originals. This doesn't happen inside the HR record's own event.
  3. The HR full listing sends an employee again once they map to a person.
  4. A model reply records every page it quotes and goes when any is deleted. In the shared KnowledgeSystem, a purge waits for an in-flight ask to finish its cache writes. It drops cached answers that touched the page and clears the configured persistent model cache even when the API runs offline.
  5. `changes_since` moves to S16b, an optional step: what it may say about deleted or revoked pages needs its own rules.
  6. Pages whose headers failed are re-processed through the same queue, on listing rounds only, when the header option is `model`.
  7. The gates live in `tests/gates/`, with no marker, and run in every pull request. S17 runs them again against Docker.
  8. The demo asks over MCP with tokens it issues itself, lucia's included. `config/`'s users are unchanged.
- **Settled while building:**
  - The re-link reuses S12's rebuild (`_reprocess`), with its own status, `relinked`, and step, `re-link`. A header retry ends as `rebuilt`.
  - "Stale" means `exact_match` would write other links today; other linkers' pages are always processed again.
  - `KnowledgeSystem` keeps its last 1,000 traces, for the demo.
  - The demo quiets the person map's repeated warning and says it once, in step 5. It exits with 1 if any promise doesn't hold.
  - While a change is applied, the inspector holds its sync loop, so "right after" is truly before the worker. A listing-only change makes the next round run the listings.
  - Every gate runs on both the `ci` and the `local` profile.
- **Re-linking processes the whole page again.** With the cache, that costs no model money, but at thousands of pages per entity change the link-only lane is better.
- **Detection scans every published chunk** for each entity change, which is fine at 50 pages. Production asks the search index for the name.
- **Exact match only.** A role ("the Head of People") or a nickname isn't found, so a page that names someone that way isn't re-linked. That is the model tier's job (card 4.10).
- **In memory.** The re-link queue and the "unmapped" report are lost on restart. So are the stores, which the full listings rebuild.
- **A failing header model is asked again every listing round** for each page without headers. Calls that fail cost nothing, but they use quota.
- **The leaver keeps her account.** Deleting the HR record doesn't touch the identity provider, so Lucía can still read until IT deactivates her (S15's scene 9).
- **One story.** The screen plays the eight scripted changes once, in order; restart the inspector to play it again. Other changes can still be made in the mocks' `/docs` pages, as before.
- **The screen asks four questions every three seconds**, each with Q4 against the mocks: fine locally, not something to leave open against real sources.

## 9. Check your understanding
1. Bob deletes Lucía's HR record. Walk through how `person:lucia` disappears from HR-PL-11's chunks: which connector sees it, what T2 decides, what the worker does after its drain, and why the page's version doesn't change. Why isn't the re-link done inside the HR record's own event?
2. Sara is added to the HR system on Monday; IT creates her account on Tuesday. Why is she no one to link to on Monday, what makes her an entity on Tuesday, and what would have happened without S16's change to the HR full listing?
3. In the Lifecycle screen, after **4. Revoke by group**, lucia's "right after" cell says `held back (denied)`, although the permission store still lists her in hr-team. Which component held the page back, and what does that column prove that "now" alone wouldn't? And when HR-PL-99 is deleted, what removes alice's model-written answer about hotels from the cache file, and why wasn't S15's tag enough?

<details>
<summary>Answers</summary>

1. The HR delta never reports a delete (S1b), so the first round sees nothing about E-1009, and the entity stays. The HR full listing compares the records it lists with the versions the change log has accepted. E-1009 is known but not listed, so it emits a `delete`. T2 decides `purge`: search (nothing, a record has no chunks), then `purge entity`, which removes `person:lucia` and returns her. The worker notes `EntityChange(lucia, None)`. After the drain, `relink()` finds HR-PL-11 as a candidate, because its chunks carry her ID, and stale, because `exact_match` against today's entities no longer links her. It processes the page again from its stored original, T3 to T8, and publishes the same version with new links: the trace says `re-link: person:lucia removed`, status `relinked`. The version is the source's and the source page didn't change. Inside the record's event, a failure on any page would bring the delete event back, have it retried and eventually park it, so the leaver would wait behind a page. After the drain, each page fails alone and stays queued, while the record is already purged.
2. On Monday the HR delta sends E-1013, and the connector maps people by full name to an identity user (D3). There is none, so the event carries no person: fail closed, and the person map reports `employee:E-1013`. The record defines no entity, because an entity ID is the identity user ID. On Tuesday the identity sync brings the user `sara`, but her HR record hasn't changed, so neither the delta nor a version comparison would send it again. The HR full listing now reloads the person map when it has reported an employee, finds that E-1013 maps to sara, and sends the same version again. T2 sees the same content hash but another person: "same content, now about sara", so it processes it. The entity store gets `person:sara`, the worker notes the change, and the re-link adds her to HR-PL-11. Without the change she would never become an entity until someone edited her HR record.
3. Q4 (`source_recheck`). Before releasing a passage it asks the identity provider for lucia's groups now and the docs tool for the page's readers now. hr-team is no longer hers, so the page is `denied`, whatever the permission store's copy says. Q2 ranked it from the stale copy, and Q4 held it back. "Right after" is asked with the sync loop held, so it proves the revocation holds on the very next request, before any connector or worker caught up (card 4.13). "Now" alone would only show the state after the sync. For the delete: the worker's purge calls `gateway.forget("hr_docs:HR-PL-99")`. The answer's reply was stored with `quotes` listing every page in its prompt, HR-PL-99 among them, and `forget` drops every reply that quotes the deleted page. S15 left answer replies untagged, because a tag means "used by": it keeps a reply while any of its documents is alive, which is right for a shared header and wrong for an answer that repeats every page it quotes. Kept, the reply couldn't be served again (its prompt can't be rebuilt), but the deleted text would still sit in the cache file.
</details>
