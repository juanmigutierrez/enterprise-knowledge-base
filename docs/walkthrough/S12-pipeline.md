# S12 · The worker pipeline: the full write path, T2 → T8 on the bus

> Architecture: `docs/architecture.md` (box 4, "Process (workers)", and the arrows "Q → T2", "T8 → publish chunk + vector + resolved ACL"). Plan: `docs/plan.md` §4.2, S12. Option card: `docs/option-evaluation.md` 4.14 (change propagation). Decisions: D4 (the change log is the replay source), D7 (records are queried, not embedded), D8 (permissions), D13 (local and production share the worker logic).

## 1. In one paragraph
Until now each step was wired by hand, inside the step before it, and every demo picked its own options. S12 turns T2 to T8 into one **worker**. It is a single consumer group, `worker`, on the change log. For each event, in its item's order, one handler runs the whole write path. T2 decides, and the event then takes one of four routes: a page goes through T3 … T8 into the search store, a record goes to its store, a change of readers alone takes the fast lane, and a delete is purged from every store. The worker chooses each step's option from `config/`. When an option isn't built yet, it uses that option's stand-in and prints the swap. It records a **trace** of every attempt (each step, what it did and how long it took), so you can follow a page from its source to searchable chunks. When an event keeps failing, the worker sets it aside in the **dead letters** after three attempts, withdraws the item from search (fail closed), and moves on. A retry later replays the parked events in order.

## 2. Where it sits in the flow
```
connectors ─▶ change log (bus, registry, raw store) ─▶ [ worker: group "worker" ] ─▶ stores
                                                         │
delivery ─▶ T2 diff ─┬ process, a page   ─▶ T8 readers first ▶ T3 ▶ T4 ▶ T5 ▶ T6 ▶ T7 ▶ T8 publish ─▶ search store
                     ├ process, a record ─▶ T3 "not a document" ▶ entity store | permission store | nothing yet
                     ├ acl_only          ─▶ T8 fast lane                                       ─▶ search store
                     ├ skip              ─▶ nothing
                     └ purge             ─▶ search, readers record, membership, entity, model cache
                     ▼
           T2 writes the registry last ─▶ the bus commits ─▶ one Trace per attempt

input:  Delivery(partition, offset, ChangeEvent)
output: the stores updated + Trace(document_key, version, partition, offset, attempt, action,
                                   status, steps=[StepTrace(step, outcome, detail, ms), …])
```

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/contracts/trace.py` | `Trace`, `StepTrace`, `StepOutcome`, `TraceStatus` (published, readers, record, skipped, purged, failed, parked, rebuilt), `STEP_ORDER`, `step_rank`. A trace holds no content. |
| `src/ekb/app/choices.py` | `choose(settings, offline=)` → `Choices`: what each step's configuration names and what runs. `STAND_INS`, `OFFLINE`, `MODEL_OPTIONS`, `Choices.with_options` (the inspector's flags), `NoStandInError` |
| `src/ekb/app/pipeline.py` | `build_changelog`, `build_stores`, `build_steps`, `build_pipeline`; `Pipeline` (`run`, `handle`, `retry_parked`, `dead_letters`), `Outcome`, `EventFailedError`, `WORKER_GROUP`, `MAX_ATTEMPTS` |
| `src/ekb/app/deadletters.py` | `DeadLetters` (park, take, put back, withdrawn items) and `Parked` |
| `src/ekb/app/connectors.py` | `Connectors`, `build_connectors`: the six connectors on one change log (moved out of the inspector, shared with the demo) |
| `src/ekb/app/inspector.py` | `SyncLoop` builds the worker instead of its own chain, and keeps the panels from its outcomes. The command reads the profile, with `--header`, `--classifier` and `--embed` overriding it, and prints the options. |
| `src/ekb/app/demos/ingest.py`, `demos/__init__.py` | `ekb demo ingest` |
| `src/ekb/operate/inspector/{trace,models,app}.py` | `TraceLog`; `GET /api/pipeline/traces` (`PipelineView`) and `GET /api/pipeline/trace` (`DocumentTraceView`) |
| `ui/src/components/PipelineScreen.tsx`, `ui/src/pipeline.ts`, `App.tsx`, `api/client.ts`, `styles.css`; `ui/openapi.json`, `src/api/schema.d.ts` | The **Pipeline** tab and the regenerated contract |
| `src/ekb/app/{chunk,parse,permissions,diff,changelog,classify,embed,entities}.py`, `process/t2_diff/step.py`, `t3_parse/step.py`, `t4_chunk/step.py`, `app/cli.py` | Docstrings: "until S12" is now "the step demos' hand wiring". `ekb worker` and `ekb connect` point to S17. |
| `tests/unit/app/test_pipeline.py`, `test_choices.py`, `test_deadletters.py`, `test_demos.py`, `test_inspector_command.py`, `tests/unit/operate/inspector/test_pipeline_panel.py`, `ui/tests/pipeline.test.mjs` | See §6 |

## 4. How it works

### One handler per event, and why not one consumer per step
`Pipeline.handle(delivery)` runs T2 with a `downstream` that does everything else. T2 already writes the registry *after* its downstream returns (S4), and the bus commits only after `handle` returns. So if T3, T7, T8 or anything else raises, T2 records nothing and the event comes back. The retry is not mistaken for a repeat:
```python
self._run = run
try:
    self.t2.handle(event)  # decide → route (T3 … T8) → observers → registry
except Exception as exc:
    ...
    return self._failed(run, exc, replay=replay)  # traced; raise (pending) or park
```
Running the whole chain inside one event is also what keeps **per-item order**: the bus delivers an item's events in order, and nothing of one event is still in flight when the next one starts. Separate topics per step (T2 → topic → T3 → topic …) would need the parsed text and the chunks stored between steps, and order kept on every topic. Here nothing is stored between steps. A retry starts again from T3, and cached model replies make that cheap (D16 #1).

### The routes
`_after_t2` is T2's `downstream`. It notes T2's decision, routes it, and lets the observers (the inspector's panels) see the outcome before T2 records the event:
```python
match decision.action:
    case DiffAction.PROCESS:
        status, readers, processed = self._process(run, event)
    case DiffAction.ACL_ONLY:
        status, readers = self._fast_lane(run, event)
    case DiffAction.SKIP:
        status = TraceStatus.SKIPPED
    case DiffAction.PURGE:
        status, removed = TraceStatus.PURGED, self._purge(run, key)
```
- **`_process`** for a page runs **readers first** (S11: a reader removed in the same save loses access before any model runs), then T3, T4, T5, T6, T7 and T8 publish. Each step goes through `run.step(name, work, detail)`, which times it and traces it as done or failed. For a record, T3 says "not a document". An HR record then goes to the entity store and an identity user to the permission store. A database row goes nowhere yet: the metrics store comes in S14b, and rows are never chunked (D7).
- **`_fast_lane`** puts the new readers on the published chunks; nothing is parsed or embedded.
- **`_purge`** empties every store: search first, which is the deletion gate (readers record and membership go with it), then the entity, then the model cache last. If any drop fails, the event comes back and every drop is repeated. Repeating a drop is harmless.

### Options from config
`choose(settings)` reads the active profile and, for every step of the write path (`PIPELINE_STEPS`), keeps the option the configuration names if it is built. If it isn't, the step's stand-in runs and the choice says why:
```
t3_parse        docling -> markdown (not built yet)
t6_entities     cascade -> exact_match (not built yet)
search          opensearch -> in_memory (not built yet)
```
`config/default.yaml` is unchanged, because a default changes only with an experiment result. `choose(…, offline=True)` swaps every model option (`t4_header: model`, `t5_classify: model_plus_rules`, `t7_embed: model`) for its offline counterpart (`none`, `rules`, `fake`). The demo runs that way unless you pass `--models`. The inspector's flags override last (`Choices.with_options`), and another option keeps none of the previous option's settings, as in the profiles.

### Traces
Each attempt produces one `Trace`: where the event sat in the bus, which attempt it was, T2's action, the final status, and the steps. A failed step is recorded with its error, and nothing after it runs:
```
hr_docs:HR-PL-04 v1.1  p0@15  attempt 1  process  published
  T2 diff             0.01 ms  process: new: never processed
  T8 readers first    0.00 ms  a new document: its readers go on with T8, after T7
  T3 parse            0.46 ms  parsed with markdown-it-py@4.3.0: 9 blocks: 5 heading, 4 paragraph
  …
  T8 publish          0.12 ms  published 4 chunks of v1.1, readers all-staff: …
```
**A trace holds no content**: only keys, versions, decisions, counts, IDs, readers and errors. The entity store step says `person:alice added`, never the name. So a deleted page's traces stay, as its history, without breaking "deleted content is never returned". A test checks that no chunk text, page title or person's name appears in any trace, before or after a delete.

### Dead letters
Full listings remember versions when the bus accepts them. A page whose first upsert is parked can therefore still generate a delete when it disappears. T2 keeps its own registry of successfully processed events.

Without dead letters, one event that always fails would block every event behind it forever, because the bus never skips (S3). `_failed` counts attempts per delivery. Below `MAX_ATTEMPTS` (3) it raises `EventFailedError` and the event stays pending. If a permissions update fails, the stores hide the affected page or membership immediately. Traces keep the failed step and exception type. On the third failure it parks the event:
```python
self._set_visible(key, False)
withdrawn = self._unpublish(key)  # a failed withdrawal leaves the delivery pending
self.dead_letters.withdraw(run.event.document_key)
self.dead_letters.park(delivery, error)  # then commit and move on
```
Four rules (`ekb.app.deadletters`):
1. **Parking fails closed.** The item's chunks leave search, and its readers record and membership go too, so no one reads a version that may be out of date.
2. **Order per item is kept.** Later events of a parked item are parked behind it, never applied around it.
3. **A delete is never parked.** It purges as usual and discards what was parked for its item. A delete that keeps failing stays pending and blocks, because a delete is never set aside.
4. **A retry replays in order.** Search and group membership stay hidden throughout recovery, including between successful replay steps. A failed rebuild stays queued for another retry. The worker uses the parked event’s lifetime and skips a rebuild if a delete has already closed it. `retry_parked()` gives each parked event one more attempt. If one fails again, it and the item's later events go back in order. If the replay republished nothing (only readers changes were parked), the item is **rebuilt** from its last stored original in the registry and raw store (D4), with status `rebuilt` and no bus position.

### The inspector and the UI
`SyncLoop` now builds the worker (`build_pipeline`) with two hooks:
- `_keep` turns each outcome into the panels it always fed (T2 decisions, parsed text, chunks, entity and publication lifetimes);
- `TraceLog.append` keeps every trace.

The round's error handling now includes `EventFailedError`. Before, a model error from T7 (a `RuntimeError`) stopped the loop's thread; now it is logged and retried next round, like any failure. The command prints the options before loading.

The **Pipeline** tab shows:
- the counters per status (of each item's latest attempt);
- a table of every step's runs, failures and time;
- every item the worker has seen;
- for the selected item, its attempts newest first, each with its route (`T2 process → T8 readers first → T3 → … → T8 publish`), its error if any, and its steps colored by part of the line.

## 5. Options
- **Built now:**
  - one in-process worker, steps in order, one consumer group;
  - in-memory dead letters.
- **Chosen from config:** every step's option and the stores' (`ekb.app.choices`). The connectors are still wired by hand (`ekb.app.connectors`).
- **Later:**
  - `ekb worker` and `ekb connect` as separate services on Kafka (S17). With the in-memory bus they must share one process, so both commands still exit with "not implemented" and point to S17;
  - dead letters through SQS's redrive policy or a Kafka topic. Neither keeps order per item by itself, which is why rule 2 lives in the worker;
  - one consumer per partition;
  - batching across documents for Bedrock batch inference (D16 #3).

No defaults changed.

## 6. What the tests prove
- **Options from config:**
  - the local profile runs its built options and the stand-ins for the rest, with the reasons;
  - settings such as `max_tokens=400` reach T4, and another option drops the previous one's settings;
  - offline swaps every model option;
  - the `ci` profile runs its own choices;
  - the inspector's flags override last;
  - an unbuilt option with no stand-in is refused;
  - a model option with no gateway is refused.
- **The write path:**
  - one group takes all 57 events of the four sources through the line: 48 chunks published, all publishable, 17 entities, 12 memberships, lag 0;
  - a page's trace lists T2 to T8 in order, with T2's reason, the chunker from config and T8's readers;
  - records go to their stores and are never chunked; a database row says why there is no store yet;
  - a record's readers change publishes nothing.
- **Each route:**
  - an unchanged save is skipped, with T2 only;
  - an edit runs readers first, then T3 to T8, and replaces the old chunks;
  - a readers-only change takes the fast lane and leaves the vectors as they were;
  - a delete runs the purge steps.
- **Deletion gate:** after a page delete, an HR record delete and an identity user delete, nothing of them is in search, the readers record is gone, no one finds the page, and the entity is gone.
- **Publication gate and at least once:**
  - when T7 fails, nothing is published or recorded: the old version stays, the event is still pending and comes back;
  - attempt 2 publishes the new version;
  - an observer that fails leaves the event unrecorded.
- **Revocation gate:** an edit that also removes a reader narrows access before T3, even while T7 is down.
- **Dead letters:**
  - an event that fails three times is parked, its item withdrawn (chunks and readers record gone), and the bus moves on;
  - other items go through, and the item's later events are parked behind it;
  - a retry replays them in order; a retry that fails again puts everything back in order;
  - a delete purges and discards what was parked; a delete that keeps failing stays pending past three attempts and is never parked;
  - a parked readers change alone ends in a rebuild from the stored original;
  - `max_attempts` must be at least 1.
- **No content in traces:** no chunk text, page title or person's name appears in any trace, before or after a delete.
- **Live loop, API, UI and demo:**
  - the loop traces every attempt, survives a model outage and retries the event next round;
  - the API lists each item's latest attempt with totals per status and per step, filters on the latest status, shows one item's attempts newest first, keeps a purged item's history, and returns 404 for an item never seen;
  - the UI helpers label statuses, steps, bus positions, times and routes;
  - the demo tells the whole story offline and leaves no subscription behind.

## 7. Run it yourself
```powershell
uv run ekb demo ingest
```
It needs no model, key or network. You should see:
1. **The options:** fourteen lines, such as `t3_parse docling -> markdown (not built yet)` and `t7_embed model -> fake (offline: no model)`.
2. **Initial load:** `The connectors put 57 events on the bus (3 partitions)`, then one line per source: 12 users go to the permission store, 17 HR records to the entity store, 16 database rows to "no store yet", and 12 pages to 48 chunks in the search store. Then how many pages each person can find.
3. **One page end to end:** HR-PL-04's trace, step by step, with times.
4. **Changes:**
   - an edit (`T2 process -> T8 readers first -> T3 -> … -> T8 publish = published`);
   - a readers change (`T2 acl_only -> T8 fast lane = readers`, `Vectors unchanged: True`);
   - an unchanged save (`T2 skip = skipped`);
   - a group change (`T2 process -> T3 -> permission store = record`, `chunks rewritten: 0`);
   - a delete (`T2 purge -> purge search -> purge entity = purged`).
5. **A poison event:**
   - runs 1 and 2: `attempt n of 3 failed at T7 embed`, with lag 2;
   - run 3: `parked after 3 failed attempts; withdrawn …`, and HR-PL-09, which waited behind it, is published (lag 0);
   - `Who finds HR-PL-06 now: no one`, then lucia's readers change is parked behind the edit;
   - the retry publishes v2.1, then applies v2.2's readers.
6. **Totals:** attempts per status, a table of steps with runs, failures and time, and `Group worker lag 0; dead letters 0`.

`uv run ekb demo ingest --models` calls the models the profile names (keys in `.env`; Ollama for T7, else fake vectors, and it says so). `EKB_PROFILE=ci uv run ekb demo ingest` shows the `ci` profile's choices (`plain_text`, `fixed`, 32-number fake vectors).

In the inspector:
1. Run `uv run ekb inspector --header none --embed fake`, then `npm run dev` in `ui/`, and open **Pipeline**.
2. Edit a page in the docs mock's `/docs` and watch a new attempt appear on its item.
3. Save the same text again and see `skipped`.
4. Change a page's readers and see the two-step route.
5. Delete a page: its history stays, ending in `purged`.

## 8. Decisions and limits
- **Settled in S12's plan (25 November 2026):**
  1. One consumer group, `worker`, runs T2 → T8 in one process for each event, and nothing is stored between steps. The inspector's group `t2_diff` is now `worker`; the step demos t2 to t8 keep `t2_diff` and their hand wiring, because they stop the line on purpose.
  2. Options come from the active profile. An option not built yet runs as its stand-in (kafka, dynamodb, s3, opensearch → in_memory; docling → markdown; cascade → exact_match), and the swap is printed; `config/default.yaml` is unchanged.
  3. `ekb demo ingest` reads the active profile and runs offline unless `--models` is given.
  4. The inspector reads the profile too; `--header`, `--classifier` and `--embed` override it. Its default classifier is therefore the profile's `model_plus_rules`, which falls back to `rules` without a key.
  5. Dead letters: an event is parked after 3 failed attempts and its item is withdrawn (fail closed). Later events of the item wait behind it. A delete is never parked and discards what was parked. A retry replays in order, with a rebuild from the stored original when needed. The dead letters live in memory in `ekb.app`, not as a change-log port.
  6. The trace model lives in `ekb.contracts`, holds no content, keeps a deleted item's history, records each step's time, and has its own **Pipeline** tab.
  7. The connectors stay wired by hand. `ekb connect` and `ekb worker` as services need a durable bus, so they move to S17.
  8. The step demos keep their own wiring; `parse_after_t2`, `chunk_after_t3` and `readers_after_t2` stay for them.
- **One worker, one thread.** `Pipeline` handles one event at a time, so it is not thread-safe. Production runs one consumer per partition. The in-memory drain stops at the first failure, which blocks every partition, not only that event's. Kafka and SQS block only the partition or message group.
- **The dead letters and the attempt counts are in memory.** When the process stops, they are lost along with the bus. A restart rebuilds from the sources' full listings.
- **No retry command.** `retry_parked()` is what the demo calls. The inspector is read-only, so it shows parked items but cannot retry them; an operator command comes with the worker service (S17).
- **Withdrawing is coarse.** A parked page is out of search entirely, even when only its newest change failed. The alternative, keeping the old version, would leave a later revocation waiting behind the parked event.
- **Records have no store for database rows yet.** They are traced as "no store yet" until the metrics store (S14b).
- **Traces are kept in the inspector's memory**, all of them, like its event log. Production sends them to OpenTelemetry (D11).

## 9. Check your understanding
1. T7's model times out on dana's edit of HR-PL-04. What is in the search store a second later, what does the registry say, and what happens on the next drain? Why must T2 write the registry *after* T3 … T8?
2. HR-PL-06's edit failed three times and was parked. Then lucia removes hr-team from its readers, then dana deletes the page. What happens to each of these two events, and who can read HR-PL-06 in between?
3. `config/default.yaml` says `t3_parse: docling`, but the demo parses with `markdown`. Where does that happen, how would you see it, and what would it take to make Docling the default?

<details>
<summary>Answers</summary>

1. The old version, v1.1, with its readers, and only narrower readers if the same save removed one, because readers first ran before T3. The registry still says v1.1: T7 raised inside T2's downstream, so T2 never got to write its record, and the bus did not commit the event (attempt 1 is traced as `failed` at `T7 embed`). On the next drain the same delivery comes back as attempt 2, T2 compares it with the registry, still sees new content, and runs T3 … T8 again. If the registry were written first, the retry would look like a repeat and be skipped, and the edit would be lost.
2. lucia's readers change is parked behind the edit (status `parked`, step `dead letters: parked behind v2.1`): applying it around the parked edit could later be undone when the edit is replayed. dana's delete is never parked: it discards the two parked events (`2 parked events discarded`), then T2 purges the page from every store. In between, no one can read HR-PL-06, because parking withdrew it from search (fail closed), and the delete then removes it for good.
3. In `ekb.app.choices.choose`: `docling` is not registered as a `t3_parse` option, so its stand-in, `markdown`, runs. The demo's first scene and the inspector's startup print `t3_parse docling -> markdown (not built yet)`, and `uv run ekb options` shows `docling [not built yet]`. To make Docling run, build it as an option (`options/docling.py`, registered as `docling`): the configuration already names it, so it runs with no config change. Making a *different* parser the default would need an experiment result (card 4.1) before `config/default.yaml` changes.
</details>
