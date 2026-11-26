# S17b · The entity and permission stores on DynamoDB

> Architecture: `docs/architecture.md` §2 (box 5: "Entity store · DynamoDB Local", "Permission store · DynamoDB Local"). Plan: `docs/plan.md` §4.2, S17. Option card: `docs/option-evaluation.md` 4.17 (entity store vs graph). Decisions: D6 (phase 1: an entity store in DynamoDB), D8 (user and group IDs; a person's groups looked up when they ask), D13 (DynamoDB Local, same code). Earlier: S9 (the entity store), S11 (the permission store, lifetimes and hidden items).

## 1. In one paragraph
S9 and S11 built two stores in memory: the **entity store** (the canonical people and teams, each defined by one HR record) and the **permission store** (who is in which group now, and T8's readers per document). S17b gives both their DynamoDB option, the design's choice for phase 1 (D6), on DynamoDB Local in `docker compose`. The rules that made them safe in memory hold unchanged: one record defines at most one entity or user, a purge removes what the record defined, an inactive or unknown person reads nothing, and a membership from a hidden item or a closed lifetime doesn't count. The one design question was how a purge finds what a record defined. A secondary index would be the textbook answer, but it can't be read consistently. Each store instead keeps a second small table, written in an order that is safe if a process stops half way.

## 2. Where it sits in the flow
```
worker: T2 ─▶ T3 "not a document" ─┬─ HR record     ─▶ EntitySync     ─▶ entity store
                                   └─ identity user ─▶ MembershipSync ─▶ permission store (memberships)
        T8 ─▶ permission store (readers per document) ─▶ search store
        purge ─▶ remove what the record defined · forget the readers record

Knowledge API: Q2 ─▶ permission store.principals(user) ─▶ the pre-filter (S13)
```
The worker writes; the Knowledge API reads. On Docker they are two processes (S17d), and DynamoDB is what they share.

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/stores/entities/options/dynamodb.py` | `DynamoEntityStore` (`entities: dynamodb`): `ekb-entities` and `ekb-entity-records` |
| `src/ekb/stores/permissions/options/dynamodb.py` | `DynamoPermissionStore` (`permissions: dynamodb`): memberships, records, hidden items, readers |
| `src/ekb/platform/aws.py` | `Table`: create on first use, strongly consistent reads, scan, delete returning the old item |
| `src/ekb/app/entities.py`, `src/ekb/app/permissions.py` | The store builders take the options' params (the namespace) |
| `src/ekb/app/pipeline.py` | `build_stores` passes each store its params |
| `tests/contract/test_knowledge_stores.py` | Both stores' promises, in memory, through moto, and against DynamoDB Local |
| `tests/fakes/aws.py` | moto clients for the unit tests |

## 4. How it works
**Four tables for the permission store**, mirroring the in-memory option's dicts and its set of hidden items:

| table | key | holds |
|---|---|---|
| `ekb-memberships` | `user_id` | the membership, as JSON |
| `ekb-membership-records` | `source_key` | the user the identity record defines |
| `ekb-membership-hidden` | `source_key` | an identity record hidden while the worker recovers it (S12) |
| `ekb-readers` | `document_key` | T8's `DocumentAcl`, as JSON |

**Why a second table instead of a secondary index.** A purge carries only the record's key (`identity:users/lucia`), and must find the user that record defined. A DynamoDB global secondary index on `source_key` would answer that, but an index can't be read strongly consistently. On real DynamoDB a purge right after a write could miss the membership, and a leaver would keep reading. The second table is read like the first. It is written before the item, and removed after it:

```python
self.records.put({"source_key": membership.source_key, "user_id": membership.user_id})
self.users.put({"user_id": membership.user_id, "doc": membership.model_dump_json()})
```

If a process stops between the two writes, the line points at a user that isn't there yet: harmless. The other order could leave a membership that no purge can find. The entity store does the same with `ekb-entity-records`.

**Whose membership counts is decided on every read**, exactly as in memory:

```python
def membership(self, user_id: str) -> Membership | None:
    member = self._stored(user_id)
    if member is None or self.hidden.get(member.source_key) is not None:
        return None
    return member if self._current(member) else None  # its lifetime is still open
```

`_current` asks the change log's acceptance log (S17a) for the identity record's lifetime. A leaver's account deleted in the identity provider stops counting as soon as the connector accepts the delete, in every process.

**A record that moved.** If another record now defines the same user (an account recreated under a new key), the newer one owns it: the old record's line goes, and a purge of the old record removes nothing. Both options keep this rule, and a test checks it.

## 5. Options
| Store | In memory (default) | `--docker` (local profile) | Later |
|---|---|---|---|
| `entities` | `in_memory` | `dynamodb` | Neptune for relations (phase 2, D6, card 4.17) |
| `permissions` | `in_memory` | `dynamodb` | DynamoDB on AWS (same code) |

The configuration always named `dynamodb`; without `--docker` they run in memory and say so.

## 6. What the tests prove
- Entities are kept by ID and listed by kind; a record defines one entity, replaced by its next version or by another ID, and its purge takes it; an ID another record took over goes only with that record. On DynamoDB, a second instance sees the same entities.
- Memberships are kept per user and replaced by a new record version; principals are the user and their groups now.
- **An inactive, unknown or purged user reads nothing** (the gates' part): `principals` returns `None`.
- **A hidden record or a closed lifetime hides the membership**, from `principals` and from `memberships()`, and releasing the record brings it back.
- Readers records are recorded, replaced, listed in key order and forgotten.
- The gates (`tests/gates/`) run the whole S16 story with these stores (`local+fakes` in every pull request; `local+docker` with `-m integration`).

## 7. Run it yourself
```powershell
make up
uv run ekb demo ingest --docker
uv run pytest -m integration tests/contract/test_knowledge_stores.py
```
`ekb demo ingest --docker` is S12's story with every store real. Step 0 prints `entities dynamodb` and `permissions dynamodb`, and each "who would find it" line is answered from DynamoDB. To look at the tables while a demo runs, list them with the AWS CLI against DynamoDB Local (`aws dynamodb list-tables --endpoint-url http://localhost:8000`). The demo's tables carry its namespace, and they are gone after it ends.

## 8. Decisions and limits
- **Settled while building:**
  - A purge finds a record's entity or user through a second table, not a secondary index: strongly consistent reads, and a write order that is safe on a crash.
  - Hidden items are a table of their own, not a flag on the membership. A flag would be lost when the worker writes the membership again during recovery, so the item would show before the worker released it.
  - The model is stored whole as JSON (`doc`), next to the key and the attributes a filter needs. Reading it back is the contract's own validation.
- **Scans.** `memberships()`, `entities()` and `all_readers()` scan their tables (a dozen users, a few dozen entities). At scale they would be queried by what the caller needs.
- **Four reads per question.** `principals` reads the membership, the hidden table and the lifetime. That is cheap on DynamoDB, and the price of deciding on every read, not caching.
- **No transactions.** The two writes of a put are ordered, not atomic. DynamoDB's `TransactWriteItems` would make them atomic, at twice the write cost.
- **DynamoDB Local runs in memory** (`-inMemory`): a restart empties it, and the full listings rebuild it (S17 plan, question 9).

## 9. Check your understanding
1. Lucía leaves: IT deletes her account, and the connector accepts the delete of `identity:users/lucia`, but the worker hasn't run yet. Which table still holds her membership, and why does `principals("lucia")` already return `None` in the Knowledge API's process?
2. Why does `put_membership` write `ekb-membership-records` *before* `ekb-memberships`? Describe what a crash between the two leaves behind in each order.
3. The worker is recovering `identity:users/alice` after a failure (S12) and has hidden it. Why would a `hidden` attribute on alice's membership item not be enough?

<details>
<summary>Answers</summary>

1. `ekb-memberships` still has her item; the worker's purge will remove it. But the acceptance log in DynamoDB now says the record's lifetime is 1, while her membership was written in lifetime 0. `_current` compares the two on every read, in whichever process asks, so the Knowledge API leaves her membership out: she reads nothing, before any purge.
2. Records first: a crash leaves a line naming a user who isn't stored yet. A purge then removes the line and finds nothing, and the next write of that record repairs it. Item first: a crash leaves a membership with no line pointing to it, and a purge, which only knows the record's key, can never find it. A leaver would keep reading until someone noticed.
3. While recovering, the worker processes alice's record again, and that writes a new membership item. An attribute on the old item would be overwritten, so alice would count again before the worker had released her. A separate table keeps the hold whatever happens to the membership, the same way the in-memory option keeps a set apart from its dict.
</details>
