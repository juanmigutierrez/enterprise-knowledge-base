# S02 · Connect + T1: the docs connector and the one change event

> Architecture: `docs/architecture.md` (box 2, "Connector service" and "T1 · Normalize"). Plan: `docs/plan.md` §4.2, S2. Option card: `docs/option-evaluation.md` 4.14 (change propagation). Related decisions: D3 (connectors behind one change-event contract, nightly full listing for deletes), D8 (the source is the authority on permissions).

## 1. In one paragraph
> **Updated in S2a:** events are now schema version 2, with `person_id` and `changed_by`; every person ID is checked by the `PersonMap`, and the demo's changes are made by named people. See `S02a-connectors.md` §4.

The docs tool speaks its own dialect: `page.created`, `page.updated`, `page.permissions_changed`, `page.deleted`, with `rev` and `acl_rev`. The record API, the database and the identity provider each speak another. If every later step had to understand all of them, every new source would mean changing every step. S2 builds the first **connector**: it listens to the docs tool and translates every change into the product's single `ChangeEvent` (**T1 · Normalize**). Two connector options work together. The **webhook** connector reacts to the source's push notifications. The **full listing** compares everything the source holds with what the knowledge base knows, and catches what the webhooks missed, above all deletes.

## 2. Where it sits in the flow
```
S1 · mock docs  ──webhook POST──▶  [ receiver → webhook connector ─┐
                ◀──GET /pages/{id}─                                ├─ T1 normalize ]  →  sink (S3: event bus)
                ◀──GET /pages────  [ full-listing connector ───────┘        │
                                                                   original bytes → OriginalStore (S3: raw store)
input:  notifications (seq, event, page_id, rev, acl_rev) and the page listing
output: ChangeEvent(source, source_id, op, version, occurred_at, acl, raw_pointer, content_hash)
```

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/connect/port.py` | `Connector` protocol (`source`, `sync()`), plus `EventSink` and `OriginalStore`, the two things injected into every connector |
| `src/ekb/connect/normalize/docs.py` | **T1** for the docs source: the translation table, `version_of` / `parse_version`, one function per event kind (S2a split `normalize` into one module per source; shared rules such as `UnknownChangeKindError` are in `normalize/common.py`) |
| `src/ekb/connect/docs_api.py` | The connector's own client for the docs tool's REST API and its own copy of the message formats (`DocsNotification`, `DocsPage` …) |
| `src/ekb/connect/options/docs_webhook.py` | `docs_webhook` option: `receive()` queues a notification, `sync()` fetches, normalizes and emits |
| `src/ekb/connect/options/docs_full_listing.py` | `docs_full_listing` option: lists the source, compares with what is known, emits the difference |
| `src/ekb/connect/receiver.py` | The webhook receiver (FastAPI): `POST /webhooks/docs` → `202 Accepted` |
| `sources/mock_docs/webhooks.py` | The mock now **pushes** webhooks: subscriptions, one best-effort POST per change, delivery counts |
| `sources/mock_docs/store.py`, `app.py` | Change listeners (called after the lock is released); `GET/POST/DELETE /subscriptions` |
| `src/ekb/app/demos/t1.py`, `_serve.py` | `ekb demo t1`; serving the receiver in a thread so it can be stopped mid-demo |
| `pyproject.toml`, `uv.lock` | `fastapi` and `uvicorn` move from dev-only to runtime dependencies: the product now serves HTTP itself |
| `tests/unit/connect/…` | Normalize, both options, receiver, all against the real mock in-process |
| `tests/sources/mock_docs/test_webhooks.py` | Webhook push: order, no content, failures don't fail the edit, no retry |
| `tests/contract/test_ports.py` | `connect` added: every connector option satisfies `Connector` |
| `tests/unit/app/test_demos.py`, `test_serve.py` | The demo story, run in-process and from the CLI |

## 4. How it works

**One event shape.** `ChangeEvent` (written in S0, `src/ekb/contracts/events.py`) has three operations: `upsert`, `delete` and `acl_change`. T1's whole job for the docs source is this table, in `normalize/docs.py`:

| Docs tool says | `op` | ACL | `raw_pointer` | `content_hash` |
|---|---|---|---|---|
| `page.created` / `page.updated` | `upsert` | current | set | current |
| `page.permissions_changed` | `acl_change` | new | none | current |
| `page.deleted` | `delete` | none | none | none |

```python
def delete_event(
    source: str, source_id: str, *, version: str, occurred_at: datetime
) -> ChangeEvent:
    return ChangeEvent(
        source=source,
        source_id=source_id,
        op=ChangeOp.DELETE,
        version=version,
        occurred_at=occurred_at,
    )
```
A delete carries nothing about the document: no pointer, no hash, no ACL. The contract already forbids a pointer on a delete; T1 also leaves the rest empty, and a test checks it.

**`version` is `"<rev>.<acl_rev>"`** (for example `"2.1"`). Downstream it is an opaque label; the bus gives the order. The full listing parses it to tell a content change from a permissions-only change.

**ACL IDs.** The directory names (`alice`, `hr-team`) are already identity provider IDs. Since S2a each user name is checked by the `PersonMap`: an unknown one is left out of the ACL and reported (fail closed).

**The webhook path, in two moments.** The mock POSTs each change to the receiver as soon as it is saved. The receiver only hands it to the connector's queue and answers `202`:
```python
def receive(self, notification: DocsNotification) -> None:
    normalize.op_for(notification.event)  # unknown kind → error now, delivery fails
    with self._lock:
        self._inbox.append(notification)
```
Later, `sync()` works through the queue. For each notification it fetches the page **as it is now**, stores the original bytes, normalizes and emits:
```python
fetched = self._api.get_page(notification.page_id)
if fetched is None:  # deleted after the notification was sent
    return gone()
...
pointer = self._store_original(f"{self._source}:{page.id}", version, fetched.original)
return normalize.upsert_event(self._source, page, raw_pointer=pointer)
```
Three choices from the plan show here:
- **The source as it is now wins.** If the page was edited again before the fetch, the event has the newer version. If it is gone, the event is a `delete` (deletes are safe to repeat).
- **A notification leaves the queue only after its event is emitted**, so a source outage during the fetch loses nothing; the next `sync()` retries.
- **An unknown change kind is an error, not a skip.** A silently dropped notification could be a delete or a revocation.

**The full listing.** `GET /pages` returns every page's IDs, versions and hashes, without bodies, so it is cheap. The connector compares it with `known` (`source_id → version`):

| Compared with what is known | Event |
|---|---|
| new, or `rev` differs | fetch → `upsert` |
| same `rev`, different `acl_rev` | `acl_change` (no fetch) |
| same version | nothing |
| known but **not in the listing** | `delete`, the case nothing else catches |

When a deleted ID is reused, the new page starts above its previous content revision. Even if both webhooks are lost, the listing fetches the replacement and applies its current readers. An ACL webhook also emits an upsert when its fetched content revision differs from the known revision. Concurrent sync calls take turns draining the queue while new deliveries can still arrive.

The source never says when a page was deleted, so `occurred_at` is when the listing noticed. `known` is injected: in the demo it is built from the events already emitted; from S3 on it will be the document registry.

**Injected, not built in.** The `sink` (where events go) and the `OriginalStore` (where originals go, returning `raw_pointer`) are plain callables. In S2 they are a list and a dict (`mem://raw/hr_docs/HR-PL-04/2.1`); in S3 they become the event bus and the raw store, and the connectors don't change.

**The mock's side.** `WebhookPublisher` keeps the subscriptions and POSTs each change once. If the POST fails, it records `failed` and `last_error` and moves on: no retry, and the edit itself still succeeds. Listeners run after the store releases its lock, because a receiver that fetches the page calls straight back into the store.

## 5. Options
Built now, both registered under step `connect`:
- `docs_webhook`: push, near real-time, can miss notifications.
- `docs_full_listing`: pull, catches everything, including deletes.

The architecture runs both: webhooks for speed, a scheduled listing for completeness (card 4.14: "webhooks + 5-minute polling fallback + nightly listing"). Later options of the same port: delta polling and full listing for the HR API, the CDC reader for the database, identity sync (all S2a). `config/default.yaml` still names `webhook_with_polling` for `connect`, shown as "not built yet" by `ekb options`. Choosing connectors per source in config comes with the pipeline wiring in S12; the demo wires the two options by hand.

## 6. What the tests prove
- **T1 table:** each docs change kind maps to its op; an unknown kind raises.
- **Deletion gate:** a delete carries no pointer, no hash and no ACL, from the webhook path, from a 404 during the fetch, and from the listing.
- **Add, edit, revoke, delete → four events**, in order, with the right versions. Revoking `hr-team` leaves only `es-employees` in the ACL. Only upserts store an original, and it is the bytes the source sent.
- **Duplicates:** the same notification twice gives two identical events.
- **Races:** an edit after the notification gives the newer version; a page gone before the fetch gives a `delete`.
- **Outage:** a failing source keeps the notifications queued and emits nothing. A delete needs no fetch at all.
- **Full listing:** nothing changed → nothing; new, edited, re-permissioned and deleted pages → the right four events; deletes found with no webhook at all; content and ACL changed together → one `upsert`; an unreadable known version → refetch; a page deleted between listing and fetch → `delete`.
- **Receiver:** `202` and hand-off; `422` for an unknown kind or a malformed body; unknown extra fields are ignored.
- **Mock webhooks:** every kind pushed in order, without content, identical to the feed; a failed delivery doesn't fail the edit and isn't retried; seeding sends nothing; subscriptions survive `/reset`; unsubscribing stops deliveries; listeners can read the store.
- **Contract:** both options satisfy `Connector`.
- **Demo:** the story runs twice in a row against the same mock, and from the CLI against a real mock process; an unreachable mock fails cleanly.

## 7. Run it yourself
```powershell
uv sync                 # fastapi and uvicorn are now runtime dependencies
uv run ekb demo t1
```
You should see:
1. Four changes in the docs tool: add `HR-PL-11`, edit `HR-PL-04`, revoke `hr-team` on `HR-PL-05`, delete `HR-PL-99`.
2. Four notifications queued by the receiver.
3. Each notification next to its event: `upsert`, `upsert`, `acl_change`, `delete`.
4. One event printed in full: the shape every source will produce.
5. The receiver stops, `HR-PL-11` is deleted, and the mock reports `failed=1`. The webhook connector has nothing to do, and the knowledge base still believes the page exists.
6. The full listing emits the missing `delete`. Run again: 0 events.

By hand:
```powershell
uv run python -m sources.mock_docs        # then http://127.0.0.1:8081/docs
uv run ekb demo t1 --url http://127.0.0.1:8081
```
Try: `GET /subscriptions` while the demo runs, or delete a page in the Swagger UI before running the demo against `--url` (the demo resets the mock first, so the page comes back).

## 8. Decisions and limits
- **Webhooks are best-effort, by design:** one synchronous POST, no retry, no signing, 127.0.0.1 only. Real tools retry a few times, sign requests and expire subscriptions; production verifies signatures and renews subscriptions.
- **Deliveries can arrive out of order** if two edits race. Each notification carries `seq`, and the fetch always returns the current page, so the last event for a page is right.
- **`known` is a dict in the demo**, rebuilt from emitted events. The document registry replaces it in S3. **Originals are kept in memory** under `mem://` pointers until the raw store exists (S3).
- **The listing fetches each new or changed page one by one.** That is fine for 12 pages; at scale it is batched and rate-limited, and run nightly (D3).
- **One docs source.** The other three source types are S2a, as options of the same port.
- **`fastapi` and `uvicorn` are now runtime dependencies** (they were dev-only in S1), so they enter the product image.

## 9. Check your understanding
1. The receiver was down while `HR-PL-11` was deleted. Which part of the system notices, and how does it know?
2. Why does `page.permissions_changed` become `acl_change` without a `raw_pointer`, and what does that let later steps do?
3. A notification says `page.updated rev=2`, but by the time the connector fetches the page it is at `rev=3`. Which version does the event carry, and why is that safe?

<details>
<summary>Answers</summary>

1. The full-listing connector. It compares the pages the source lists now with `known`, what the knowledge base believes exists (the document registry from S3). `HR-PL-11` is known but not listed, so it emits a `delete`, with the time it noticed, because the source never says when.
2. Only the permissions changed (`acl_rev` rose, `rev` did not), so the content and its copy are unchanged and nothing needs to be re-read, re-parsed or re-embedded. T2's fast lane (S4) can update the ACL on the existing chunks directly. A delete carries no pointer for a different reason: nothing about deleted content may travel further.
3. `3.1`, the version fetched. The event describes the source as it is now. The `rev=3` edit sends its own notification, which produces the same event again, and T2 skips the repeat by comparing content hashes. Emitting the older content instead could make the knowledge base go backwards.
</details>
