# S03 · Change log: event bus, document registry, raw store

> Architecture: `docs/architecture.md` (box 3, "Event backbone and raw store": Kafka locally, EventBridge + SQS FIFO on AWS; the DynamoDB document registry; S3 / SeaweedFS originals). Plan: `docs/plan.md` §4.2, S3. Option card: `docs/option-evaluation.md` 4.15 (event backbone). Related decisions: D4 (one event-bus interface; on AWS, replay from the originals and the registry), D3 (the full listing is compared with the registry), D13 (local stand-ins).

## 1. In one paragraph
Until now a connector handed its events to a list, its originals to a dict, and compared its full listing against a dict rebuilt from events. S3 replaces all three with the **change log**, the backbone between the connectors and the workers. It has three parts. The **event bus** keeps every event and delivers it to each reader (a *consumer group*) in order per document, at least once, and never skips one. The **document registry** remembers, per item, the last change the knowledge base processed. That is what T2 will compare new events with, and what the full listings compare the sources with. The **raw store** keeps the exact bytes each source sent, one immutable object per version, so no later step, and no replay, has to ask the source again. All three are in memory for now; Kafka, DynamoDB Local and SeaweedFS slot in behind the same ports in S17.

## 2. Where it sits in the flow
```
connectors (S2, S2a) ──publish──▶ event bus: 3 partitions, by sha256(document_key)
      │                              │ poll / commit, per consumer group
      │ store_original               ├─ registry   stand-in worker → document registry (T2 from S4)
      ▼                              ├─ inspector  the inspector's copy (ekb inspector, UI)
   raw store ◀──── get(pointer) ─────└─ reindex    a replay: a new group reads from the start
      ▲
  full listings ── known(source) ──▶ document registry (live items, tombstones left out)

input:  ChangeEvent (T1)
output: Delivery(partition, offset, event) per group · DocumentRecord per item · stored originals
```

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/changelog/bus/port.py` | `EventBus` (`publish`, `poll`, `commit`, `stats`), `Delivery`, `BusStats`, `GroupPosition`; the three promises in the docstring; `OutOfOrderCommitError`, `UnknownDeliveryError` |
| `src/ekb/changelog/bus/options/in_memory.py` | `in_memory` option: one list per partition, committed offsets per group; `partition_for()` |
| `src/ekb/changelog/bus/consume.py` | `drain()`: handle an event, commit it, then the next; stops at the first failure |
| `src/ekb/changelog/registry/port.py` | `DocumentRecord` (a tombstone may hold no content), `record_after()` (what each op does to a record), `DocumentRegistry` (`get`, `put`, `known`, `records`), `record_event()` |
| `src/ekb/changelog/registry/options/in_memory.py` | `in_memory` option: a dict by `document_key` |
| `src/ekb/changelog/rawstore/port.py` | `RawStore` (`put`, `get`, `versions`, `purge`), `StoredOriginal`, `OriginalNotFoundError`, `ConflictingOriginalError` |
| `src/ekb/changelog/rawstore/options/in_memory.py` | `in_memory` option: immutable objects under `mem://ekb-raw/<source>/<id>/<version>` |
| `src/ekb/connect/port.py` | `EventSink` now accepts a callable that returns something (`bus.publish` returns the `Delivery`) |
| `src/ekb/app/changelog.py` | `ChangeLog`: the three in-memory parts wired together; `known(source)`; `record_processed()`, the stand-in worker |
| `src/ekb/app/demos/s3.py` | `ekb demo s3`: publish, record, crash and redeliver, refuse a skipped commit, replay without the source |
| `src/ekb/app/inspector.py` | The connectors now publish to the bus and compare against the registry; each round records to the registry and fills the inspector's log through its own group |
| `src/ekb/operate/inspector/{log,models,app}.py` | The log keeps each event's partition and offset; `GET /api/changelog`, `/api/registry`, `/api/originals` |
| `ui/src/components/ChangeLogScreen.tsx`, `App.tsx`, `EventTable.tsx`, `format.ts`, `styles.css`, `api/client.ts` | **Change log** tab: partitions and groups, the registry, an item's originals; a `bus` column (`p0@7`) on every events table |
| `ui/openapi.json`, `ui/src/api/schema.d.ts` | Regenerated contract |
| `tests/unit/changelog/…` | Bus, `drain`, registry records, registry, raw store |
| `tests/contract/test_ports.py` | `bus`, `registry` and `rawstore` added: every option satisfies its port |
| `tests/unit/app/test_demos.py`, `test_inspector_command.py`, `tests/unit/operate/inspector/test_inspector.py` | The S3 story; the inspector's round with the change log; the new endpoints |

## 4. How it works

**The bus: partitions, offsets, groups.** A partition is an ordered list; an event's *offset* is its position in it. `publish` puts an event in the partition of its document:
```python
def partition_for(document_key: str, partitions: int) -> int:
    digest = hashlib.sha256(document_key.encode()).digest()
    return int.from_bytes(digest[:8], "big") % partitions
```
A cryptographic hash, not Python's `hash()`: Python salts string hashes per process, and a document must land in the same partition every time. (Kafka does the same with murmur2.) With 3 partitions the 12 HR pages split 6 / 3 / 3.

A **consumer group** is a reader with its own position: one committed offset per partition. `poll(group)` returns everything after the group's commits, oldest first, and does *not* move it; only `commit` does. That gives the three promises in `bus/port.py`:

1. **Order per document.** One document, one partition, delivered in publish order. Two documents in different partitions have no order between them, and nothing needs one: each document has its own history.
2. **At least once.** A worker that crashes before committing gets the same events again. So a worker can see an event twice; T2 will skip repeats by hash (S4).
3. **No skipping.** Commits go strictly in order within a partition:
   ```python
   if delivery.offset > next_offset:
       raise OutOfOrderCommitError(... "skipping it could lose a delete or a revocation")
   ```
   A delivery carrying the wrong event for its position is refused. Committing an already committed event does nothing (a repeat is harmless).

`drain(bus, group, handle)` is the loop every consumer uses: handle one event, commit it, next. If `handle` raises, the drain stops; that event and everything after it stay pending for the next drain.

**Replay is a new group.** A group that has never committed starts at offset 0 in every partition, the earliest event the bus holds. The demo's `reindex` group reads all 16 events while the `registry` group, already at the end, is unaffected. That works because the in-memory bus keeps everything (Kafka keeps events until retention or compaction removes them). SQS forgets a message once it is consumed, which is why D4 makes the raw store plus the registry the replay source on AWS.

**The registry: what was processed.** One `DocumentRecord` per item. `record_after(previous, event)` is the whole rule:

| Event | Record afterwards |
|---|---|
| `upsert` | everything from the event; a deleted item comes back |
| `acl_change` | new readers and version; a matching original keeps its pointer. If the original is missing or the reported hash differs, the registry clears the pointer and requests a fresh copy |
| `delete` | **tombstone**: version, time, who deleted; no hash, readers, pointer or person (the validator refuses any) |
| `acl_change` on a tombstone | stays a tombstone: new readers are no proof the content is back (fails closed) |

`known(source)` gives `source_id → version` of the live items, tombstones left out. A live record without a usable pointer gets a `NeedsOriginal(version)` entry. The full listing fetches that item even at the same version, or emits a delete with its last observed version if it has disappeared. So in the demo the second listing finds nothing, and after the delete the listing no longer counts `HR-PL-99` as existing.

Who writes it? The registry holds the last *processed* state, and there is no worker yet. Until T2 exists, `ChangeLog.record_processed()` is a stand-in worker: consumer group `registry` drains the bus and records every event. In S4, T2 takes over that group and decides first (skip, fast lane, purge).

**The raw store: originals, immutable.** `put(document_key, version, bytes)` has exactly the connectors' `OriginalStore` shape, so `changelog.rawstore.put` is passed to them as is. The pointer names the item and version, each part URL-encoded because IDs and versions contain `/` and `:`:
```
mem://ekb-raw/hr_docs/HR-PL-04/1.1
mem://ekb-raw/hr_api/employees%2FE-1001/2026-11-24T09%3A00%3A00%2B00%3A00
```
The same bytes again return the same pointer (a duplicate event is harmless). Different bytes under a stored version raise `ConflictingOriginalError`: a version must name one content, or T2 would skip a real change as "unchanged". `purge(document_key)` removes every version; nobody calls it yet. T2 does, when it processes a delete (S4).

**Wiring** (`src/ekb/app/changelog.py`): the full listings and docs webhook now use the registry to decide when to fetch an original.

| Connector needs | Now | In S2 |
|---|---|---|
| `sink` | `changelog.bus.publish` | a list |
| `store_original` | `changelog.rawstore.put` | a dict of `mem://raw/...` bytes |
| `known` | `changelog.known(source)` (the registry) | a dict rebuilt from events |

**The inspector** is now just another consumer group. Each round of `ekb inspector`: frequent connectors → the `registry` group records their events → the full listings, when due, compare against the registry → `registry` again → the `inspector` group copies every new event into its log. A failing group is logged and retried next round; its events stay pending. The new **Change log** tab shows each partition's size and each group's committed offset and pending count, the registry with tombstones struck through, and, for a selected item, the originals the raw store holds (metadata only, never the bytes).

## 5. Options
Built now: `bus: in_memory`, `registry: in_memory`, `rawstore: in_memory` (all three satisfy their port in `tests/contract/`). Later options of the same ports: Kafka, DynamoDB Local and the S3 API on SeaweedFS (S17); SQS FIFO only if the AWS deployment is built (D18). `config/default.yaml` is unchanged (`kafka`, `dynamodb`, `s3`, shown by `ekb options` as "not built yet", with `in_memory` available): changing a default needs an experiment result. The demo and the inspector wire the in-memory options by hand; choosing them in config comes with the pipeline wiring (S12).

## 6. What the tests prove
- **Order per document:** a document's events share one partition, offsets 0, 1, 2; the partitioner gives the same answer in every process; 12 pages spread over all partitions; a truncated poll keeps each document's order; 4 threads × 100 events lose nothing and keep each page's order.
- **At least once:** polling twice without a commit returns the same events; after a crash, only the uncommitted events come back; groups are independent; a new group replays the full history.
- **No skipping (deletion gate):** committing past a pending delete is refused and nothing moves; committing an offset the bus never handed out is refused; a repeat commit is harmless; `drain` stops at a failing event and leaves it, and the delete after it, pending.
- **Deletes stay empty:** an `acl_change` and a `delete` pass through the bus unchanged; a tombstone holds no hash, pointer, readers or person, and the model refuses one that does; `known` leaves tombstones out; an `acl_change` doesn't bring a deleted item back; an upsert does.
- **Registry:** each op's effect; an ACL change keeps a compatible pointer, and both full listings recover missing content or tombstone a vanished item; latest state per item; records sorted and filtered.
- **Raw store:** bytes come back exactly; `/` and `:` are encoded and can't collide; the same bytes again are a no-op; different bytes are refused and the original stays; versions oldest first with size and checksum; `purge` removes every version of one item only.
- **Inspector:** one round loads 57 events, records them all in the registry before the listings run, and both groups end at lag 0; an HR delete becomes a tombstone; a failing consumer group is retried next round with nothing skipped; events carry partition and offset; the three endpoints; originals never include the bytes; `ui/openapi.json` matches the API.
- **Demo:** the story runs twice in a row in-process, and from the CLI.

## 7. Run it yourself
```powershell
uv run ekb demo s3
```
You should see:
1. The initial load: the full listing publishes 12 events into 3 partitions (6 / 3 / 3), and 12 originals are stored.
2. The `registry` group has 12 pending, records them, and the next listing sends 0 events.
3. Four changes by webhook, with where each landed: `p0@6` and `p0@7` are the two `HR-PL-04` edits (v2.1, v3.1), `p1@3` the revoke on `HR-PL-05`, `p0@8` the delete of `HR-PL-99`.
4. A crash: after a restart the same 3 uncommitted events come back, and committing the delete before `p0@7` is refused.
5. The registry: `HR-PL-05` at v1.2 still points to its 1.1 original; `HR-PL-99` is a tombstone.
6. With the docs tool stopped, group `reindex` replays 16 events, reads 14 originals from the raw store, and rebuilds exactly the registry's state.

In the inspector (`uv run ekb inspector`, then `npm run dev` in `ui/`), open **Change log**. Edit a page in the docs tool's `/docs` page and watch the edited page’s partition grow and both groups catch up; click the item to see a second original appear. Delete it: the row becomes a struck-through tombstone, and its originals stay until T2 purges them (S4).

## 8. Decisions and limits
- **Kafka's model, in memory.** One consumer per group (no rebalancing across workers); `poll` restarts from the committed offset each time, where Kafka also keeps a fetch position in the consumer. No retention, no compaction: everything is kept, and lost when the process stops.
- **A failing event stops its group's drain.** Correct (nothing skipped) but a poison event would block for ever; production moves it to a dead-letter queue after a few attempts.
- **The registry is written by a stand-in** until T2 (S4). **Purge exists but isn't called** until T2, so a deleted page's originals remain in the raw store for now. Agents never read the raw store.
- **A version must name one content.** HR API versions are timestamps; two different saves with the same timestamp would make the connector fail loudly that round. Accepted: silent overwrites would break T2's "unchanged" check.
- **Connector cursors are still in memory**; keeping them durably, so a restarted connector resumes, comes with the durable stores (S17).
- **`EventSink`'s return type was widened** so `bus.publish` (which returns the `Delivery`) is a sink.
- **The S2 demos (`ekb demo t1`) are unchanged:** they still use a list and dicts, to keep the S2 story about T1 only.

## 9. Check your understanding
1. `HR-PL-04` is edited twice and `HR-PL-05` is revoked in between. What order is guaranteed when a worker reads them, and what isn't?
2. A worker handles `p0@7`, then crashes before committing it. What happens next, and why does the bus refuse to let it commit `p0@8` first?
3. The docs tool is down and you must re-embed everything with a new model. Where do the texts come from, and what would differ on AWS?

<details>
<summary>Answers</summary>

1. Both `HR-PL-04` events are in the same partition (sha256 of the key), so they are delivered in publish order: v2.1 before v3.1, always. `HR-PL-05` is in another partition, so its event may be handled before, between or after them. That's fine: ordering only matters within one document's history.
2. Nothing was committed for `p0@7`, so the next poll delivers it again (at least once), and the worker must cope with seeing it twice (T2 skips repeats by hash). Commits go strictly in order per partition: committing `p0@8` while `p0@7` is pending would mark `p0@7` done without ever handling it, and a skipped event could be a delete or a revocation, so the bus raises `OutOfOrderCommitError`.
3. From the raw store: a new consumer group replays every event from the start, and each upsert's `raw_pointer` is read from the raw store, with no request to the source (the demo does it with the docs tool stopped). On AWS the bus is SQS, which keeps nothing once consumed, so the replay walks the registry's live items and reads their originals from S3 instead (D4).
</details>
