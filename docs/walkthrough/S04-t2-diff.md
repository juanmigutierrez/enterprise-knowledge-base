# S04 · T2 Diff: skip what hasn't changed, fast-track permissions, purge deletes

> Architecture: `docs/architecture.md` (box 4, "T2 · Diff vs registry: skip unchanged · ACL fast lane · delete purges"). Plan: `docs/plan.md` §4.2, S4. Option card: `docs/option-evaluation.md` 4.14 (change propagation: the deleted-content gate, and the cost of re-processing avoided by hash checks). Related decisions: D3 (the full listing catches deletes), D4 (an at-least-once bus), D8 (permissions), D16 #1 and #8 (never pay twice for the same content).

## 1. In one paragraph
T2 is the first worker step. The change log (S3) delivers every event at least once, the webhook and the full listing can both report the same change, and the docs tool raises a page's version on every save, even when the text is identical. Without T2, every one of those events would be parsed, chunked, classified, enriched and embedded again, at a cost (D16), for the same result. T2 asks one question per event: compared with what the knowledge base last processed for this item (its registry record), what is left to do? There are four answers. **process**: the content is new or changed, so T3 to T8 run. **acl_only**: only the readers changed, so the fast lane goes straight to T8. **skip**: nothing new. **purge**: the item was deleted, so remove it everywhere. T2 also takes over writing the registry from S3's stand-in worker, and on a delete it purges the item's originals from the raw store. Two answers can never come out wrong: a delete is always purged, and a revocation is never skipped.

## 2. Where it sits in the flow
```
change log (S3) ──poll, group t2_diff──▶ [ T2 ]
                                           1. decide(registry record, event)   the option; pure
                                           2. purge originals (deletes only)   raw store
                                           3. downstream(result)               nothing yet; S12: T3, T8, stores
                                           4. record_after(previous, event)    registry
                                           5. commit                           bus (drain)

input:  ChangeEvent + DocumentRecord | None
output: DiffDecision(action, reason, event, previous_version), inside DiffResult(decision, purged)
```

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/contracts/diff.py` | `DiffAction` (`process`, `acl_only`, `skip`, `purge`) and `DiffDecision`; its validator makes "a delete is always purged" and "only an upsert is processed" part of the contract |
| `src/ekb/process/t2_diff/port.py` | `Differ.decide(previous, event) -> DiffDecision`: pure, no I/O |
| `src/ekb/process/t2_diff/options/content_hash.py` | `content_hash` option: the decision table; `readers_diff()` (`+es-employees -hr-team`) |
| `src/ekb/process/t2_diff/step.py` | `DiffStep.handle(event)`: decide, purge, hand on, record; `DiffResult`; which originals a delete purges |
| `src/ekb/changelog/rawstore/port.py`, `options/in_memory.py` | `purge(document_key, lifetime=...)`: removes every original in the lifetime closed by the delete |
| `src/ekb/app/diff.py` | `T2_GROUP = "t2_diff"`, `diff_step()`, `run_t2()` |
| `src/ekb/app/inspector.py` | The sync loop runs T2 in place of S3's stand-in; its decisions go to a `DecisionLog` |
| `src/ekb/app/changelog.py`, `demos/s3.py` | The stand-in stays for `ekb demo s3` only (its replay reads every original, including the deleted page's) |
| `src/ekb/app/demos/t2.py` | `ekb demo t2` |
| `src/ekb/operate/inspector/{log,models,app}.py` | `DecisionLog`, `DecisionView`, `DiffCounts`, `GET /api/t2/decisions` |
| `ui/src/components/DiffScreen.tsx`, `ActionBadge.tsx`, `diff.ts`, `format.ts`, `App.tsx`, `styles.css`, `api/client.ts` | **T2 Diff** tab |
| `ui/openapi.json`, `ui/src/api/schema.d.ts` | Regenerated contract |
| `tests/unit/process/t2_diff/test_content_hash.py`, `test_step.py` | One test per row of the table; side effects, crash safety, purges |
| `tests/unit/test_contracts.py`, `changelog/rawstore/test_in_memory.py`, `contract/test_ports.py` | The decision contract; `purge(lifetime=)`; `t2_diff` satisfies its port |
| `tests/unit/app/test_demos.py`, `test_inspector_command.py`, `operate/inspector/test_inspector.py` | The demo story; T2 in the inspector's loop; the new endpoint |
| `ui/tests/diff.test.mjs` | Runs avoided, action order, version labels |

## 4. How it works

**Compare what a reader sees, not version labels.** Versions are opaque and differ per source (`2.1`, an ISO timestamp, a log sequence number), and a source may raise one without changing a word. Every upsert carries a `content_hash` of what a reader sees (the docs tool's title and body; a record's canonical JSON, S2), and every event carries its readers (`acl`). So the `content_hash` option compares those two with the registry record:

| event | registry before | action | why |
|---|---|---|---|
| delete | anything | **purge** | never skipped |
| upsert | none, or a tombstone | process | new, or back after a delete |
| upsert | live, but no original processed | process | its content was never processed |
| upsert | live, other or no hash | process | content changed |
| upsert | live, same hash and person, other readers | acl_only | readers changed |
| upsert | live, same hash, other person (any readers) | process | now about someone else (T6, T8 use it) |
| upsert | live, all the same | skip | a repeat, or a new version with the same text |
| acl_change | a tombstone | skip | stays deleted (fails closed) |
| acl_change | same readers as last processed | skip | a repeat |
| acl_change | anything else | acl_only | the fast lane |

The order of the upsert checks matters. "No original processed" comes before the hash check: an ACL change for an item the registry had never seen leaves a live record with a hash but no pointer (S3), and the upsert that later brings the original has that same hash. Comparing hashes first would skip content that was never processed.
```python
if previous.raw_pointer is None:
    return DiffAction.PROCESS, "its content was never processed, only its readers"
if event.content_hash is None or event.content_hash != previous.content_hash:
    return DiffAction.PROCESS, f"content changed: hash {...} -> {...}"
if event.person_id != previous.person_id:
    return DiffAction.PROCESS, "same content, now about another person"
if event.acl != previous.acl:
    return DiffAction.ACL_ONLY, f"same content, readers changed ({readers_diff(...)})"
```

**A revocation is never skipped.** An ACL change is skipped only when the readers are exactly those last processed, or the item is deleted. An ACL change whose hash differs from the registry's (the content changed too, but the event carries no original) still takes the fast lane, so the new readers apply now. The registry then clears the pointer (S3), `known()` reports the item as needing its original, and the next full listing fetches it as an upsert, which is processed.

**Two rules live in the contract.** `DiffDecision` refuses a delete that isn't purged, a purge that isn't a delete, and a `process` for anything but an upsert (only an upsert points to an original). No future T2 option can break the deletion gate by mistake.

**The step: decide, purge, hand on, record.** `DiffStep.handle` is the same for every option:
```python
previous = self._registry.get(event.document_key)
decision = self._differ.decide(previous, event)
purged = 0
if decision.action is DiffAction.PURGE:
    purged = self._rawstore.purge(event.document_key, lifetime=event.original_lifetime)
result = DiffResult(decision, purged)
self._downstream(result)
self._registry.put(record_after(previous, event))
return result
```
The registry is written **last** because T2 recognises a repeat by comparing with it. If the record came first and the worker crashed before the next step had the decision, the redelivered event would look like a repeat and be skipped, and the change would be lost. Written last, a crash means the event is decided and handed on again, which is harmless. If `downstream` raises, nothing is recorded and `drain` doesn't commit, so the event comes back. `record_after` is S3's rule, unchanged: a test checks T2 leaves exactly the registry the stand-in did.

**Which originals a delete purges.** Each document starts in lifetime 0. The connector stores each original through `ChangeLog.store_original`. When `ChangeLog.publish` publishes a delete, it stamps the event with the current lifetime and then advances the document to the next one. A re-created item gets its own pointer, even when the source reuses a version label.

T2 removes every original with the deleted key and lifetime. This includes originals stored out of event order and bytes whose upsert never reached the bus. If downstream fails after the purge, the bus redelivers the same lifetime. A second purge removes zero originals. The next lifetime stays readable throughout.

**Wiring.** `ekb.app.diff` builds the step over the change log's registry and raw store and drains group `t2_diff`. In `ekb inspector`, T2 replaces the stand-in's `registry` group: after each group of connectors T2 runs before the full listings compare with the registry, and each result is appended to a `DecisionLog` (T2's `downstream` there). `ekb demo s3` keeps the stand-in, as the S2 demos kept their lists: its replay reads every original, including the deleted page's, which T2 would have purged.

**The inspector.** `GET /api/t2/decisions` returns the decisions (filtered by `source`, `action`, `document_key`, after a cursor) and the totals per action (never filtered). The **T2 Diff** tab shows the totals and how many T3–T7 runs were avoided (`acl_only + skip`), every decision newest first with its reason (skips greyed), and, for a clicked item, all its decisions in order.

## 5. Options
Built now: `t2_diff: content_hash`, which `config/default.yaml` already named, so `ekb options` now shows it as ready. The demo and the inspector wire it by hand; choosing it from config comes with the pipeline (S12), which also replaces the do-nothing `downstream` with T3, T8 and the stores. Later: a `process_all` baseline for experiment 4.14, to measure the re-processing T2 avoids. Not T2 options: the DynamoDB registry and S3 raw store (S17), and reusing vectors of unchanged chunks after an edit (T7's cache, D16 #8).

## 6. What the tests prove
- **Deletion gate:** a delete is purged whatever the registry holds (live, never processed, already deleted); the contract refuses any other action for a delete; the originals are gone and the record is a tombstone with no hash, pointer or readers.
- **Revocation gate:** narrowing the readers takes the fast lane; an ACL change is skipped only for identical readers or a deleted item, and never brings a deleted item back; one that reports new content still applies the readers.
- **Skips:** the same event twice, and the webhook's and the listing's copies of one edit, are processed once; a new version with the same text is skipped but still moves the registry to that version, so the next listing emits nothing.
- **Never-processed content:** an upsert after a permissions-only record is processed even with a known hash.
- **Crash safety:** if the next step fails, nothing is recorded; a crash before recording means a redelivery is processed again, not skipped; a crash after recording makes it a skipped repeat; in the inspector, a failed T2 round records nothing and the retry processes all 57 items.
- **Purges:** `tests/unit/app/test_diff.py` publishes versions in a different order from storage, queues a re-creation with a reused version, and fails downstream after the first purge. Redelivery preserves the new original. The step tests also exercise a delete after an ACL change clears the registry pointer.
- **Same registry as S3:** T2 records exactly what the stand-in recorded for the same events.
- **Inspector and demo:** the endpoint's rows, reasons, filters and totals; the contract in `ui/openapi.json` is current; the demo story runs twice in a row, in-process and from the CLI.

## 7. Run it yourself
```powershell
uv run ekb demo t2
```
You should see:
1. The initial load: 12 pages, all `process` ("new: never processed"); the next listing finds nothing.
2. dana edits `HR-PL-04`; the webhook and the listing both report it before T2 runs: `p0@6 … -> process  content changed: hash … -> …`, then `p0@7 … -> skip  repeat: v2.1 was already processed`.
3. dana saves `HR-PL-01` with the same text: `v2.1 -> skip  new version, same content and readers as v1.1`, and the listing stays quiet.
4. lucia removes hr-team from `HR-PL-05`: `acl_change … -> acl_only  readers changed (-hr-team)`.
5. dana deletes `HR-PL-99` and re-creates it before T2 runs: originals `1.1` before, `1.1, 2.1` after; T2 purges 1 original, then processes the re-creation; `2.1` is left. A final delete purges it and leaves a tombstone.
6. The totals: 19 decisions, `process 14, acl_only 1, skip 2, purge 2`: 3 runs of T3–T7 avoided.

In the inspector (`uv run ekb inspector`, then `npm run dev` in `ui/`), open **T2 Diff**. Save a page in the docs tool's `/docs` page without changing its text and watch a grey `skip` appear; change its readers for an `acl only`; delete it for a `purge` with "1" in the purged column. Click a row to see the item's decisions in order. The **Change log** tab now shows the group `t2_diff` where S3 had `registry`.

## 8. Decisions and limits
- **The hash covers what a reader sees, nothing else.** A change to something outside it (a page's department label, if the docs tool allowed editing it) would be skipped. The connectors decide what goes into the hash (S2).
- **The purge reaches the raw store only.** There is no search index yet; `downstream` does nothing until S12 routes `purge` to the stores (S11). The deletion gate is proven end to end in S16.
- **No stale-version check.** Versions are opaque, so T2 relies on the bus's per-document order instead of comparing versions.
- **A change about another person, with the same text, is processed even if the readers also change** (rare; records use no model, D16 #6).
- **Decisions are kept in memory, by the inspector only.** The S3 demo keeps the stand-in, so `ekb demo s3` still says its deleted page's original is stored.

## 9. Check your understanding
1. The docs tool saves `HR-PL-01` with identical text, raising its version from 1.1 to 2.1. What does T2 decide, what does it still write, and why does that matter to the full listing?
2. Why does T2 write the registry after handing the decision on, and not before? What would go wrong with the other order?
3. dana deletes `HR-PL-99` and re-creates it before T2 runs. Why doesn't T2 purge every original of `HR-PL-99`, and how does it know which one to keep?

<details>
<summary>Answers</summary>

1. `skip`: the hash and the readers are the same as v1.1's, so parsing and embedding it again would produce the same chunks. T2 still records v2.1 in the registry. The full listing compares versions with the registry; if the registry stayed at 1.1, every listing would report the page as changed again.
2. Because T2 recognises a repeat by comparing with the registry, and the bus delivers at least once. If the record were written first and the worker crashed before the next step got the decision, the redelivered event would match the registry, be skipped as a repeat, and the change would never reach T3 or T8. Written last, a crash only means the event is decided and handed on again, which every step must accept anyway.
3. The connector stored the re-creation's original when it reported it, and that upsert is queued behind the delete; purging it would leave the upsert pointing at nothing. The delete names the lifetime containing 1.1. The original for 2.1 belongs to the next lifetime, so it survives even if T2 retries the delete.
</details>
