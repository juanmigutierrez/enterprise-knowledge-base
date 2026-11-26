# S17a · The durable change log: Kafka, DynamoDB and S3 behind the same ports

> Architecture: `docs/architecture.md` §2 (box 3: "Apache Kafka, KRaft, 1 node", "Document registry · DynamoDB Local", "SeaweedFS, S3 API"). Plan: `docs/plan.md` §4.2, S17 (split into S17a–S17d). Option card: `docs/option-evaluation.md` 4.15 (event backbone). Decisions: D4 (an event-bus interface with two implementations; Kafka locally), D13 (DynamoDB Local, SeaweedFS), and S3's and S11's walkthroughs (ordering, lifetimes).

## 1. In one paragraph
Until now the change log lived in one Python process: lists for the bus, dicts for the registry and the raw store. That is why the connectors, the worker and the Knowledge API all had to run in that one process. S17a gives box 3 its real options, behind the ports S3 wrote: **Kafka** for the bus, **DynamoDB** for the document registry, and the **S3 API on SeaweedFS** for the raw store. It also moves the change log's last private state into a new port, the **acceptance log**: each item's lifetime, its latest event, and what the bus has accepted. Nothing above box 3 changes. The same behaviour suites run against the in-memory options, the real options over fakes, and (with `-m integration`) the real services in `docker compose`. Everything still runs in memory by default; `--docker` switches boxes 3 and 5 to the real options.

## 2. Where it sits in the flow
```
connectors ──publish──▶ ChangeLog ──▶ bus ............ Kafka topic ekb.changes (3 partitions)
                           │   └────▶ acceptance log . DynamoDB table ekb-accepted   (new)
            store_original └────────▶ raw store ...... S3 bucket ekb-raw (SeaweedFS)
                                         ▲
worker (T2) ── registry ................ DynamoDB table ekb-registry
            ── poll / commit ........... Kafka consumer group "worker"
search, permission stores ── current_lifetime(key) ── acceptance log
```
input: `ChangeEvent`s and original bytes, as before · output: the same `Delivery`, `DocumentRecord` and pointers, now kept outside the process.

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/changelog/bus/kafka.py` | `KafkaLog`, the six log operations the bus needs, and `ConfluentLog`, the only module importing confluent-kafka |
| `src/ekb/changelog/bus/options/kafka.py` | `KafkaEventBus` (`bus: kafka`): the port's three promises on top of `KafkaLog` |
| `src/ekb/changelog/acceptance/` | The new port `AcceptanceLog` (`Accepted`, `accepted_after`), options `in_memory` and `dynamodb` |
| `src/ekb/changelog/registry/options/dynamodb.py` | `DynamoRegistry` (`registry: dynamodb`) |
| `src/ekb/changelog/registry/port.py` | `known_versions`, shared by both registry options and the acceptance log |
| `src/ekb/changelog/rawstore/options/s3.py` | `S3RawStore` (`rawstore: s3`): one object per version and lifetime |
| `src/ekb/platform/infra.py` | Endpoints from `EKB_*` variables, the namespace, `InfrastructureError` |
| `src/ekb/platform/aws.py` | boto3 behind `Table` and an S3 client; the only module importing boto3/botocore; `AwsError` |
| `src/ekb/app/changelog.py` | `ChangeLog` keeps its lifetimes in the acceptance log; `accepted(source)` for the connectors |
| `src/ekb/app/choices.py` | `INFRA_STEPS`, `choose(…, docker=, namespace=)`, `Choices.on_docker` |
| `src/ekb/app/pipeline.py` | `build_changelog` builds the acceptance log from the `registry` choice |
| `config/profiles/ci.yaml` | Boxes 3 and 5 named `in_memory`, so CI never needs Docker |
| `pyproject.toml`, `uv.lock` | Extras `kafka`, `search`, `aws`; the dev group installs them and moto; three import-linter contracts |
| `compose.yaml` | SeaweedFS in the core; DynamoDB Local pinned (3.3.2) with a health check |
| `tests/contract/test_bus.py` | The bus's promises, on `in_memory`, Kafka over a fake log, and real Kafka |
| `tests/contract/test_changelog_stores.py` | Registry, acceptance log and raw store, in memory, through moto, and real |
| `tests/unit/changelog/bus/test_kafka.py` | What is Kafka's alone, and the confluent-kafka adapter against stand-in clients |
| `tests/fakes/` | `FakeKafka`, moto clients |
| `src/ekb/app/demos/s3.py` | `ekb demo s3 --docker` |

## 4. How it works
**The bus keeps its promises in Python, not in Kafka's consumer API.** Kafka's consumer is built for groups of consumers that share partitions and rebalance. The port is smaller: a group is a named position that only moves on `commit`. So the bus uses Kafka as a log, through six operations (`KafkaLog`): append, read a range, look at the watermarks, and read and write a group's committed offsets. A poll reads from the committed offset and never moves it:

```python
position = self._position(group)  # remembered, or asked of Kafka once
marks = self._log.watermarks(self.topic, self._partitions)
starts = {p: max(position[p], marks[p][0]) for p in range(self._partitions)}
ends = {p: min(marks[p][1], starts[p] + max_events) for p in range(self._partitions)}
records = self._log.read(group, self.topic, starts, ends)
```

and a commit refuses to skip, before Kafka is asked:

```python
if offset > next_offset:
    raise OutOfOrderCommitError(...)  # an earlier event could be a delete
self._log.commit(group, self.topic, partition, offset + 1)
```

The partition is chosen by the bus with the same `partition_for` (SHA-256 of `document_key`) as the in-memory bus, so a document lands in the same partition in both options and in every process. `ConfluentLog` assigns partitions explicitly, never subscribes, and never auto-commits. It publishes synchronously, with `acks=all` and an idempotent producer, because the change log needs the offset before it records the event as accepted.

**Kept forever, not compacted.** The topic is created by the bus with `cleanup.policy=delete` and unlimited retention. A new consumer group replays the whole history, as in memory. Compaction (keep each document's latest event) bounds the log at scale, but a replay then sees only the latest state.

**The acceptance log: why S11's lifetimes needed a port.** A delete closes its item's lifetime the moment the bus accepts it. The search and permission stores then hide anything from the closed lifetime, before any worker has purged it (the deletion gate). Until S17 those lifetimes were a dict in `ChangeLog`. With the connectors in one process and the API in another, the API would never see a lifetime close. Now `ChangeLog.publish` writes them through the acceptance log:

```python
lifetime = self.current_lifetime(key)  # acceptance.get(key).lifetime, or 0
delivery = self.bus.publish(event.model_copy(update={"original_lifetime": lifetime}))
self.acceptance.accept(delivery)  # publish first, then accept
```

Publish first, then accept: if a process stops between the two, the acceptance log still lists the item, so the next full listing sends its delete again. The other order could hide a page for a delete the bus never got.

The worker can finish a delivery before the connector records its offset in DynamoDB. Kafka orders each document's events on one partition. Once the worker finishes recovery, it releases the document unless the acceptance log already records a later offset for it.

**DynamoDB items.** Every table has a string key, and the model is stored whole as JSON in `doc`, next to the few attributes a filter needs (`source`, `deleted`). Reads are strongly consistent, so a process sees what another has just written. `Table` creates the table on first use.

**S3 objects.** `<source>/<source ID>/<lifetime>/<version>`, each part URL-encoded, under `s3://ekb-raw/…`. `put` asks first whether the object exists, so the same bytes return the same pointer and other bytes are refused. `purge` lists and deletes the item's objects.

**The namespace.** Every name the real options create starts with the namespace: as written (`ekb.changes`, `ekb-registry`) in the default `ekb`, prefixed in any other. A demo or test run on Docker takes a fresh one (`demo-1a2b3c4d`) and drops it at the end (`ekb.app.infra.run_namespace`), so runs never see each other's data.

## 5. Options
| Step | In memory (default) | `--docker` (local profile) | Later |
|---|---|---|---|
| `bus` | `in_memory` | `kafka` | SQS FIFO (D4, D18) |
| `registry` + acceptance log | `in_memory` | `dynamodb` | DynamoDB on AWS (same code) |
| `rawstore` | `in_memory` | `s3` (SeaweedFS) | S3 with versioning |

`config/default.yaml` is unchanged: it always named `kafka`, `dynamodb` and `s3`. Without `--docker`, `choose()` swaps them for `in_memory` and says so (`in memory: no --docker`), as `--models` works for models. The `ci` profile names `in_memory` itself, so even `--docker` stays in memory there. `ekb options` now shows the three as ready.

## 6. What the tests prove
- `tests/contract/test_bus.py`: on every option, a document always lands in one partition in order; polling doesn't move a group; uncommitted events come back; a commit can't skip a pending one or name an event the bus never handed out; groups are independent; a new group replays everything; events come back equal (deletes stay empty). On the durable option, a restarted process resumes where its group committed.
- `tests/contract/test_changelog_stores.py`: on every option, the registry keeps one record per item and empty tombstones; a delete closes the lifetime and moves the latest delivery; new readers for a deleted item leave it deleted; an original comes back byte for byte, can't change once stored, and is gone after a purge (one lifetime or all). A second instance sees the same state.
- `tests/unit/changelog/bus/test_kafka.py`: the topic is created once, in the namespace, with unlimited retention; a topic with another partition count is refused; a broker that can't be reached raises `InfrastructureError` and loses nothing; the adapter reads exactly the asked range (skipping end-of-partition notices and read-ahead), times out instead of waiting forever, and turns Kafka's errors into `InfrastructureError`.
- `tests/unit/app/test_choices.py`: without `--docker` boxes 3 and 5 run in memory and say why; with it the real options run, in the given namespace; `ci` stays in memory.
- The integration run (`make up`, then `uv run pytest -m integration`) runs the same suites against Kafka, DynamoDB Local and SeaweedFS.

## 7. Run it yourself
```powershell
make up                              # or: docker compose up -d --wait
uv run ekb demo s3 --docker
uv run pytest -m integration tests/contract/test_bus.py tests/contract/test_changelog_stores.py
```
The story is S3's, on Kafka, DynamoDB and SeaweedFS. Step 0 now names the topic, the bucket and the table. Step 6 replays from **a second set of clients**, opened as another process would. Everything it reads (the events, the registry, the originals) was kept by the services, not by the demo's memory. Without `--docker` the demo is S3's, in memory. Leave `make up` running and look at what a run left behind: nothing, because the run's namespace is dropped at the end.

## 8. Decisions and limits
- **Settled in S17's plan (26 November 2026):**
  1. S17 is split into S17a–S17d, each with its own demo, walkthrough and review. Postgres + Debezium and Keycloak are left for an optional S17e.
  2. Boxes 3 and 5 run in memory unless `--docker`; `ci.yaml` names `in_memory`; no default changed.
  3. The raw store is the `s3` option on SeaweedFS.
  4. The change log's lifetimes, latest deliveries and accepted records move into the acceptance log, with a DynamoDB option. Publish first, then accept.
  5. One topic, `ekb.changes`, 3 partitions, `cleanup.policy=delete`, unlimited retention.
  6. Libraries: confluent-kafka, opensearch-py and boto3 as extras, installed by the dev group; moto for DynamoDB and S3 in unit tests.
- **Settled while building:**
  - Each option imports its library only when it is built, so `ekb` imports without the extras. Three import-linter contracts keep confluent-kafka, boto3 and opensearch-py in one module each.
  - The options turn "can't reach the service" into `InfrastructureError`, a `ConnectionError`, so the sync loops log it and try again, and a demo says `make up`.
  - Building an option opens no connection; the topic, tables and bucket are created on first use.
  - Endpoints are environment variables, not config: they depend on where the process runs (host or Compose), and they are not choices.
- **One writer.** Only the process running the connectors writes the acceptance log and the raw store, so their read-then-write needs no conditional write. Several connector processes would need DynamoDB's conditional writes and S3's `If-None-Match`.
- **Scans.** `known` and `records` scan their table; a secondary index on `source` would replace that at scale (and be eventually consistent, which `known` can afford, the gates can't).
- The Kafka contract tests exercise replay, offsets and partition ordering against the Compose broker. Its host listener uses `127.0.0.1:29092`. The adapter retries watermark reads during a new partition's leader election.
- **Connector cursors stay in memory** (S2a deferred them here). A restarted connector starts with a full listing, which is correct, only slower.

## 9. Check your understanding
1. The worker polls the `worker` group, handles three events and crashes before committing the third. A new worker process starts. Which offset does it read from, where does that number come from, and why does `poll` never move it?
2. Bob deletes HR-PL-99 in the docs tool. The connector publishes the delete to Kafka, and then the container is killed before `acceptance.accept` runs. What does a search for HR-PL-99 return in the meantime? What brings everything back in line, and why is this order safer than accepting first?
3. You run `ekb demo s3 --docker` while `docker compose --profile app up` is serving agents. Why can't the demo's events reach the services' worker, and why does that matter for both?

<details>
<summary>Answers</summary>

1. From the third event's offset: the group's committed position, which Kafka stores per group and partition (`ConfluentLog.committed`). A new process has no memory of its own, so `KafkaEventBus._position` asks Kafka once. `poll` reads from there and never commits: only `commit` moves a group, one event at a time and in order. So anything not committed is read again, by this process or the next, and the worker must accept repeats (T2 skips them by content hash).
2. The acceptance log still has HR-PL-99's old lifetime, so the stores don't hide it yet: a search can still find it until the worker purges it. The worker does get the delete from Kafka and purges, and Q4 holds it back meanwhile, because the docs tool no longer has the page. The next full listing compares the docs tool with the acceptance log, finds HR-PL-99 known but not listed, and sends the delete again; that one is accepted and closes the lifetime. If the change log accepted first and then failed to publish, the lifetime would close for a delete the bus never got: the page would vanish from search although it still exists, and nothing would ever process that delete.
3. The demo runs in its own namespace (`demo-…`): its topic is `demo-…-ekb.changes` and its tables are `demo-…-ekb-registry` and the rest, so the services, which use `ekb`, never see them. For the services, a demo's edits and deletes would otherwise change what the agents are told. For the demo, the services' events would be mixed into its story. The demo then drops its namespace, so nothing is left behind.
</details>
