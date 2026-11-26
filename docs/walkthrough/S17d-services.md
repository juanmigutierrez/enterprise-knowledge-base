# S17d · Three services on Docker: `docker compose --profile app up`

> Architecture: `docs/architecture.md` §2 (the "Connector service", "worker containers, same code" and "Knowledge API container" boxes). Plan: `docs/plan.md` §4.2, S17. Option cards: 4.13 and 4.14 (the gates, now against Docker too). Decisions: D4 (several independent consumers of one stream), D9 (the Knowledge API over MCP), D13 (local Docker Compose, same image for every role), and `docs/repo-structure.md` §4 (one image, several roles).

## 1. In one paragraph
Up to S16 the whole POC was one process (`KnowledgeSystem`): connectors, worker and Knowledge API met in the in-memory change log, so they could not run apart. With S17a–c everything they share is outside the process. S17d splits the POC into the three services the architecture draws: **`ekb connect`** publishes every change to Kafka, **`ekb worker`** runs T2 … T8 into OpenSearch and DynamoDB, and **`ekb api --query-only`** serves the HR assistant over MCP. All three come from the one image, next to the four mock sources in their own small image, started together by `docker compose --profile app up`. One piece needed a new consumer: the answer cache lives in the API process now, so the API reads the deletes off the bus with its own consumer group. The gate tests run the S16 story on the real options too: over fakes in every pull request, and against Docker in the integration job. `ekb demo infra` runs the story in memory and on Docker and puts the two side by side.

## 2. Where it sits in the flow
```
mock-docs  mock-hr-api  mock-idp  mock-hr-db (SQLite file on a volume)     ekb-sources image
    │ webhooks, delta, listings, CDC, identity sync
    ▼
connector  (ekb connect) ──▶ Kafka ekb.changes · DynamoDB ekb-accepted · S3 ekb-raw
                                  │ group "worker"                 │ group "answer-cache"
                                  ▼                                ▼
worker     (ekb worker)  ──▶ OpenSearch ekb-chunks · DynamoDB stores
                                  ▲
api        (ekb api --query-only) ── Q1 … Q4 ── HR assistant ── MCP on 127.0.0.1:8080 ◀── agent
           └─ Q4 asks mock-docs and mock-idp now                                       ekb image
```

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/app/services.py` | `ConnectService`, `WorkerService`, `QueryService`, `run_rounds`, the `answer-cache` group |
| `src/ekb/app/serve.py` | The `ekb connect` and `ekb worker` commands |
| `src/ekb/app/api.py` | `ekb api --query-only` (the third service) and `--docker` (one process, real infrastructure) |
| `src/ekb/app/infra.py` | `require_shared` (a service refuses to run in memory), `run_namespace`, `drop` |
| `src/ekb/app/demos/infra.py` | `ekb demo infra`: the S16 story in memory and on Docker, side by side |
| `src/ekb/app/demos/{ingest,search,answer,mcp,lifecycle}.py`, `inspector.py` | `--docker` |
| `compose.yaml` | The `app` profile: four mocks, `connector`, `worker`, `api`; volumes `ekb-cache` and `hr-db` |
| `deploy/docker/sources.Dockerfile` (+ `.dockerignore`, `sources-requirements.txt`) | The mocks' image, dependencies pinned to `uv.lock` with hashes |
| `deploy/docker/Dockerfile` | The real infrastructure's clients by default; `/app/.cache` owned by the app user |
| `sources/common/factory.py` | The mocks' `--host` (127.0.0.1 unless told otherwise) |
| `tests/gates/conftest.py` | The gates in four places: `ci`, `local`, `local+fakes`, `local+docker` |
| `tests/fakes/infra.py` | `fake_infrastructure`: every real option's client pointed at a fake |
| `tests/unit/app/test_services.py`, `test_demos_infra.py` | The three services over fakes; the S17 demos |

## 4. How it works
**Each service is the one-process system's pieces, wired apart.** They use the same builders as `KnowledgeSystem`:

```python
class WorkerService:
    def __init__(self, write: Choices, gateway: ModelGateway | None = None) -> None:
        require_shared(write, "ekb worker")
        self.changelog = build_changelog(write)  # its own Kafka client, its own tables
        self.stores = build_stores(write, self.changelog)
        self.pipeline = build_pipeline(write, self.changelog, stores=self.stores, gateway=gateway)
```

Nothing is shared in memory: the worker's bus is a Kafka client, its acceptance log a DynamoDB table, its search store an OpenSearch client. `require_shared` refuses to start a service whose boxes 3 or 5 would be in memory (the `ci` profile), because its writes would be invisible to the others.

**Rounds that fail come back.** Each service calls its `round` every few seconds (`run_rounds`). The connector's frequent connectors run every round, and the full listings run on the first round and then every tenth. The worker drains its group, then re-links. A round that fails (a mock or a service down) is logged and tried again next round. Nothing is lost, because the bus commits nothing that wasn't done. `docker compose stop` sends SIGTERM, and the service finishes its round first.

**The answer cache gets its own consumer group.** In one process, the worker's purge emptied the answer cache (S16). Now the cache is in the API process, so the API reads the bus too:

```python
def round(self, *, listings: bool = False) -> int:
    return drain(self.changelog.bus, ANSWER_GROUP, self._forget_deleted)


def _forget_deleted(self, delivery: Delivery) -> None:
    if delivery.event.op is ChangeOp.DELETE:
        self.forgotten_answers += self.assistant.cache.forget(delivery.event.document_key)
```

This is what Kafka gives that SQS doesn't (D4's reason to move to MSK): several independent readers of the same stream, each at its own position. Meanwhile a cached answer is still re-checked by Q4 before reuse (S15), so nothing deleted is served in between.

**Deletes cross processes through the acceptance log.** When the connector accepts a delete, it closes the page's lifetime in DynamoDB. The API's OpenSearch store asks for that lifetime on every read, so the page stops being returned at once, before the worker purges it. `tests/unit/app/test_services.py` checks exactly that with three services sharing the fakes.

**The Compose file.**
- *Environment, not config.* The services get `EKB_KAFKA_BOOTSTRAP=kafka:9092`, `EKB_DOCS_URL=http://mock-docs:8081` and so on; on the host the defaults are the published ports.
- *Webhooks.* The connector listens on `0.0.0.0:8090` inside the network, and subscribes `http://connector:8090/…` with the docs tool.
- *The HR database* is a SQLite file the `mock-hr-db` container creates in the `hr-db` volume, read by the connector's CDC reader.
- *`ekb-cache`* holds the model cache, the cost log and the test tokens, shared by the worker and the API.
- *Ports* bind to `127.0.0.1` only: 8080 (MCP), 8081–8083 (the mocks), and the infrastructure's.

## 5. Options
| How it runs | Command | Infrastructure |
|---|---|---|
| One process, in memory (as before) | `ekb api`, `ekb demo …`, `ekb inspector` | none |
| One process, real infrastructure | the same with `--docker` | `make up`; a namespace per run |
| Three services | `docker compose --profile app up -d --build` | the `ekb` namespace, kept while the stack runs |

`EKB_MODELS=true` in `.env` makes the worker and the API call the profile's models (keys in `.env`; Ollama through `--profile models`).

## 6. What the tests prove
- `tests/gates/`: the deletion, revocation, publication and leakage gates and the whole S16 story hold in four places: `ci` and `local` in memory, `local+fakes` (Kafka, DynamoDB, S3 and OpenSearch code over fakes) in every pull request, and `local+docker` (`-m integration`) against `docker compose`.
- `tests/unit/app/test_services.py`: a change flows from the connector through the worker to the API, three services sharing only the infrastructure. **A delete hides the page from the API before the worker purges it**, and the API's group empties the cached answer that cited it. A service refuses to start with its change log or stores in memory (`ekb worker`, `ekb connect` and `ekb api --query-only` exit 2 on `ci`).
- `tests/unit/app/test_demos_infra.py`: the S16 story keeps all 16 promises in memory and on the real options, and cites the same pages; `ekb demo s3 --docker` reads its history back from a second set of clients.

## 7. Run it yourself
```powershell
docker compose --profile app up -d --build --wait
docker compose logs api            # the test tokens and the command that connects Claude Code
docker compose logs -f worker      # T2 … T8 at work
uv run python -m agents.hr_assistant --token <alice's token> "How many remote days can I take?"
docker compose --profile app down
```
Then change a source the way its owner would, and ask again a few seconds later:
```powershell
curl -X DELETE http://127.0.0.1:8081/pages/HR-PL-99 -H "X-Actor: dana"
```
The side-by-side story, without the stack (only `make up`):
```powershell
make up
uv run ekb demo infra
```
It runs the S16 story twice. It prints every promise in each place, how many replies cite the same pages, and where the Docker run's data lived (events in the topic, items in the tables, chunks in the index). It exits 1 if a promise fails anywhere.

## 8. Decisions and limits
- **Settled in S17's plan (26 November 2026):**
  1. The API reads the deletes with its own consumer group (`answer-cache`) and empties its answer cache; the model cache and the cost log are shared by the worker and the API on one volume.
  2. One small image for the four mocks; the HR database on a shared volume.
  3. Everything is temporary: `down` and `up` start clean, and the full listings rebuild.
  4. Keycloak, Postgres + Debezium, durable cursors, a retry command for parked events and OIDC tokens stay for later.
- **Changed while building, to flag:** the plan said the test tokens would sit in a `.cache/` folder mounted from your machine. They are in the `ekb-cache` **named volume** instead. SQLite (the model cache) is not safe on a Windows bind mount, and a container user can't write a Linux host's folder without a `chmod`. The API prints the tokens at start (`docker compose logs api`), and `docker compose exec api cat .cache/ekb/test-tokens.json` shows them.
- **Settled while building:**
  - The mocks listen on `0.0.0.0` only with `--host` (inside a container); the default stays `127.0.0.1`.
  - The API binds `0.0.0.0` inside its container (`EKB_BIND`), but still checks the `Host` header for `127.0.0.1`/`localhost` (DNS-rebinding protection, S15), and Compose publishes it on `127.0.0.1` only.
  - `ekb api --docker` and every `--docker` demo run in a namespace of their own, so they never mix with the stack's `ekb` namespace.
  - The gates on fakes add about 90 seconds to the pull-request tests, mostly moto answering DynamoDB calls.
- Compose builds each shared image once. The worker owns the product image build and mock-docs owns the source image build. During review, the stack started and indexed all 48 synthetic chunks.
- **One connector, one worker, one API.** Scaling the worker means Kafka consumer groups with rebalancing (the bus assigns every partition to one consumer today), and several connectors would need conditional writes (S17a).
- **Dead letters, traces and the answer cache are per process, in memory**, as in S12 and S15: Kafka retains parked events, but the worker commits their offsets when it parks them. Restarting loses the in-memory retry queue. The same consumer group resumes after those offsets.

## 9. Check your understanding
1. Dana deletes HR-PL-99 while the stack runs. List, in order, which service does what, with which consumer group, until alice's next question cites HR-PL-06 instead. Which step makes HR-PL-99 disappear from her answers first?
2. Why can't `ekb worker` run on the `ci` profile, and what would go wrong if `require_shared` didn't stop it?
3. In one process, the worker's purge emptied the answer cache (S16). Why couldn't the split system keep that design, and what does the `answer-cache` consumer group cost and gain?

<details>
<summary>Answers</summary>

1. The docs tool posts a webhook to the connector; the connector fetches the page (gone) and publishes a delete to `ekb.changes`, then accepts it in `ekb-accepted`, which closes HR-PL-99's lifetime. From that moment the API's OpenSearch store leaves HR-PL-99 out of every read (the ranking snapshot excludes closed lifetimes), so it is gone from alice's answers first. Q4 would hold it back anyway, because the docs tool no longer has it. The API's `answer-cache` group reads the delete and forgets every cached answer that touched the page. The worker's `worker` group reads the same delete: T2 purges, removing the chunks from OpenSearch, the readers record, and the original from S3. Alice's next question finds HR-PL-06 and cites it.
2. On `ci` boxes 3 and 5 are in memory, so the worker's bus, registry and stores would be its own Python objects. It would read no events from the connector, and write chunks no API could see. The stack would look healthy and answer from nothing. `require_shared` makes that a clear error (exit 2) instead.
3. The cache is a Python object in the API process, and the purge runs in the worker process, which can't reach it. The group costs one more reader of the topic and a few polls per minute. It gains independence: the API forgets as soon as the delete is on the bus, even before the worker runs, and a slow or crashed worker doesn't delay it. It is what D4 names as the reason to move to Kafka (MSK) at scale.
</details>
