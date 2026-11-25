# S11 · T8 Permissions and the search store: why nothing is searchable before permissions are set

> Architecture: `docs/architecture.md` (box 4, "T8 · Resolve permissions"; box 5, "Search index" and "Permission store"; the arrow "T8 → publish chunk + vector + resolved ACL"). Plan: `docs/plan.md` §4.2, S11. Option cards: `docs/option-evaluation.md` 4.13 (permission enforcement) and 4.14 (the publication test). Decisions: D8 (permissions copied into the index as user and group IDs, used as a pre-filter), D3 (one canonical person ID), S1c (a leaver is deactivated, not deleted).

## 1. In one paragraph
Until now every chunk left T7 with a sensitivity and a vector but **no readers**, so none could be searched. T8 closes the write path. It copies each page's readers from the source (canonical user and group IDs, already mapped by T1) onto every chunk of that page. It then publishes the chunks to the **search store**, but only chunks that have readers, a sensitivity and a vector: the **publication gate**. The search store checks the gate a second time when the chunks arrive. Groups stay groups. Who is in a group lives in the **permission store**, kept current by the identity provider's user records through the change log. It is looked up when someone asks, so a group change takes effect on the next request without rewriting a single chunk, and a leaver reads nothing, whatever their groups say. A change of readers alone takes T2's **fast lane** straight to T8, and nothing is parsed or embedded again. A delete takes the page out of search.

## 2. Where it sits in the flow
```
documents:  T2 ── process ──▶ [T8 readers first] ─▶ T3 ▶ T4 ▶ T5 ▶ T6 ▶ T7 ─▶ [ T8 publish ] ─▶ search store
            T2 ── acl_only ─▶ [T8 fast lane]     ───────────────────────────────────────────────▶ search store (readers only)
            T2 ── purge ────▶ forgetter ─▶ unpublisher: search store + readers record (+ membership)
identity:   T2 ── process ──▶ T3: "not a document" ─▶ membership sync ─▶ permission store (user → groups, active)

T8 publish:  resolution = resolver.resolve(event, memberships)     # source_acl: the event's readers, and notes
             chunks     = with_readers(chunks, record, event)               # acl, acl_version, resolver_id on each
             index.publish(key, chunks)                              # the gate, again; one version replaces the last
             permissions.put_readers(record)                         # DocumentAcl: readers, version, when

input:  Chunk(…S6–S10 fields…, sensitivity=internal, vector=(…), acl=None)            ← not publishable
output: Chunk(…, acl=Acl(groups={"hr-team","es-employees"}), acl_version="1.1",
              resolver_id="source_acl@1")                                              ← publishable, published

asking (S13 builds on this):  principals = permissions.principals("alice")           # None if inactive/unknown
                              index.readable_by(principals)                          # admits(chunk.acl, principals)
```

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/contracts/permissions.py` | `Principals` (a user ID and their groups now), `Membership` (one identity user: active, groups), `admits(acl, principals)` (the one rule), `readers_now(acl, memberships)`, `DocumentAcl` (a document's readers, their source version and time) |
| `src/ekb/contracts/knowledge.py` | `Chunk.acl` documented; new `Chunk.acl_version` and `Chunk.resolver_id` |
| `src/ekb/stores/search/{port,options/in_memory}.py` | `SearchStore`: `publish` (the gate, all or nothing, one version replaces the last), `set_readers` (the fast lane), `remove`, `chunks`, `documents`, `readable_by(principals)`; `UnpublishableError`, `Published` |
| `src/ekb/stores/permissions/{port,options/in_memory}.py` | `PermissionStore`: memberships (`put_membership`, `remove_membership`, `membership(s)`, `principals`) and readers records (`put_readers`, `readers`, `all_readers`, `forget_readers`) |
| `src/ekb/process/t8_permissions/port.py` | `PermissionResolver`, `MembershipDirectory`, `Resolution` |
| `src/ekb/process/t8_permissions/options/source_acl.py` | `t8_permissions: source_acl`: the source's readers as written, with notes on unknown groups, unknown users and leavers |
| `src/ekb/process/t8_permissions/step.py` | `PermissionStep.publish` / `apply_readers` (fast lane) / `readers_first`; `check_input`, `with_readers`, `T8Action`, `PermissionResult`, `describe`, `readers_diff` |
| `src/ekb/process/t8_permissions/{records,sync}.py` | `idp_user` (a stored identity user → `Membership`); `MembershipSync` (handle, forget) |
| `src/ekb/app/permissions.py` | The wiring: `search_store()`, `permission_store()`, `resolver()`, `permission_step()`, `membership_sync()`, `readers_after_t2()` (fast lane + readers first), `unpublisher()` (the purge) |
| `src/ekb/app/chunk.py` | `chunk_after_t3(…, t8=, memberships=)`; `Processed.published` and `Processed.membership`; `final` returns the published chunks |
| `src/ekb/app/demos/t8.py`, `demos/__init__.py` | `ekb demo t8` |
| `src/ekb/app/inspector.py`, `operate/inspector/{permissions,models,app}.py` | T8 in the live loop; `PermissionPanel`; `GET /api/t8/permissions` (`PermissionsView`, `PublicationView`, `MembershipView`) |
| `ui/src/components/PermissionsScreen.tsx`, `permissions.ts`, `App.tsx`, `styles.css`, `ChunkScreen.tsx`; `ui/openapi.json`, `src/api/schema.d.ts` | The **T8 Permissions** tab; the regenerated contract; chunk eligibility on the T4 tab |
| `tests/unit/test_permissions_contract.py`, `tests/unit/stores/{search,permissions}/`, `tests/unit/process/t8_permissions/`, `tests/unit/app/test_permissions.py`, `test_demos.py`, `test_inspector_command.py`, `tests/unit/operate/inspector/test_inspector.py`, `tests/contract/test_ports.py`, `ui/tests/permissions.test.mjs` | See §6 |

## 4. How it works

### Readers on the chunk, groups looked up when someone asks
A page's readers arrive with its change event (`ChangeEvent.acl`): user IDs and group IDs, already canonical. T1 mapped each user to the identity provider's ID and left out any it couldn't map (D3, fail closed). T8 copies them onto every chunk:
```python
out = tuple(
    chunk.model_copy(
        update={
            "acl": record.acl,
            "acl_version": record.acl_version,
            "resolver_id": record.resolver_id,
        }
    )
    for chunk in chunks
)
```
It does **not** expand `hr-team` into bob, dana and lucia. Who is in a group comes from the permission store, at the moment someone asks:
```python
def admits(acl: Acl, principals: Principals | None) -> bool:
    if principals is None:  # unknown, or a leaver: nothing
        return False
    return principals.user_id in acl.users or not acl.groups.isdisjoint(principals.groups)
```
That is the whole rule, in `ekb.contracts`, so T8's "who can read it now", the search store's filter and Q2's pre-filter (S13) all use the same one. It is also how OpenSearch filters, with a `terms` query on `acl.users` and `acl.groups` (S17). The alternative, storing every reader as a user, would make a group change a rewrite of every chunk that group can read. It is kept as a later option (`expand_groups`, card 4.13).

### The publication gate, twice
`PermissionStep.publish` refuses chunks that are not ready for it: chunks from another version, chunks with no sensitivity ("T5 runs before T8"), chunks with no vector ("T7 runs before T8"), and chunks that already have readers (a new version is a new set). After the readers are on, every chunk must be publishable. The search store then checks again at the door:
```python
if not chunk.publishable:
    raise UnpublishableError(f"{chunk.chunk_id} is not publishable: missing readers (T8)")
```
If one chunk fails, the whole batch is refused and the store keeps the old version. A chunk in the index is a chunk someone can find, so a bug upstream must not be able to put one there.

### One version at a time
`publish(document_key, chunks)` replaces *all* the document's chunks. `chunk_id` names a place (`hr_docs:HR-PL-01#2`), not a text, so a shorter new version leaves no stale passage behind. A version with no chunks removes the document from search and still records its readers.

### The fast lane, and readers before content
When only the readers changed, T2 decides `acl_only`. `readers_after_t2` sends that straight to `PermissionStep.apply_readers`, which calls `index.set_readers(record)` and writes the readers record. Only `acl`, `acl_version` and `resolver_id` change; the text and the vector stay. `acl_version` is then newer than `document_version`, and the UI shows that as "changed by the fast lane".

An edit can also change the readers in the same save (T2 then decides `process`). `readers_after_t2` calls `readers_first` *before* T3: a revocation takes effect immediately on the published chunks. If the edit adds a user or group, T8 removes the previous version from search. The new audience can search the replacement after T7 succeeds and T8 publishes it, even if processing needs a retry. An ACL notification with a different content hash follows the same rule. A new document has no readers record yet, so `readers_first` does nothing and T8 publishes it after T7.

### Memberships from the identity provider
The identity sync (S2a) emits one upsert per user change: a joiner, a leaver, a group change. Each user is one tracked item (`identity:users/alice`). T2 processes it like any record, T3 reports "not a document", and `MembershipSync.handle` reads the stored user record (`idp_user`) and puts a `Membership` in the permission store. This is the same path the entity store takes in S9. Nothing else happens: no chunk is touched. The demo shows alice removed from `it-employees` losing three pages with `Chunks rewritten: 0. Texts embedded: 0.`

A leaver is deactivated, not deleted (S1c). Their membership stays with its groups, `active=False`, and `Membership.principals` is `None`: they read nothing.

### What `source_acl` resolves
For the docs tool there is nothing left to translate: its readers are flat lists, already canonical. The option exists so that a source with richer permissions (Confluence space permissions intersected with page restrictions, SharePoint inheritance) can flatten them in an option of its own. It reports what is worth a look and keeps the readers as written:
- **an unknown group** (no identity user is in it) is kept and reported. It admits nobody, and when the group gets members they get what the source granted, without reprocessing the page;
- **an unknown user or a leaver** is reported; `admits` already gives them nothing;
- **an empty ACL** is copied and reported ("it names nobody, so no one can read it"). The chunk is published and readable by no one, which is what the source said.

T8 never narrows readers because of the **sensitivity**. T5 labels from the content (S8). Rules such as "this agent never reads confidential HR data" are Cedar policies, applied when someone asks (Q2, S13 and S15).

### Deletes
`unpublisher` is added to the purge's `forgetter`. It removes the document from the search store first (the deletion gate), then its readers record, and for a purged identity record the membership it defined. When the change log accepts a delete, its lifetime check immediately hides the old chunks from search or the old identity from membership lookups and the inspector. T2 can purge the stored data afterward. Re-creating the same key starts a new lifetime.

### The inspector and the UI
`ekb inspector` now runs T8 after T7, with the fast lane in front of T3, and prints `T8 published 48 of 48 chunks with their readers (source_acl@1); 12 active users in the permission store.` `GET /api/t8/permissions` returns:
- per document: status (`published`, `readers_only`, `waiting`), chunks, text and readers versions, readers, unknown groups, and who could read it now;
- per user: active or not, groups, and how many documents they could read.

The **T8 Permissions** tab shows the documents with a detail pane, a users × documents grid ("who can read what now") and the users table, with leavers struck through. The T4 chunk badge shows whether the processed chunk is publishable. The T8 tab shows whether it is currently in search.

## 5. Options
Built now, chosen by hand in `ekb.app.permissions` (choosing from config comes with the pipeline, S12):
- `t8_permissions: source_acl` (the name `config/default.yaml` already used);
- `search: in_memory` and `permissions: in_memory`. The config still names `opensearch` and `dynamodb` (S17); `ekb options` shows them as not built yet, with `in_memory` available.

Later: `expand_groups` (every reader stored as a user, to compare with card 4.13's "list everything user X can see"); a Cedar option that narrows readers by sensitivity; OpenSearch and DynamoDB (S17). No defaults changed.

## 6. What the tests prove
- **The rule:** a person is admitted by name or through a group they are in now; an ACL naming nobody admits nobody. **Leaver gate:** an inactive or unknown person has no principals and reads nothing, though they keep their groups. `readers_now` lists only active readers.
- **Search store:** publishing stores a document's chunks in order. **Publication gate at the door:** a batch with one chunk missing its readers, sensitivity or vector is refused whole, and the old version stays. Chunks of another document, two versions or one ID twice are refused. A new version replaces every old chunk; no chunks removes the document. `set_readers` changes only the readers. **Deletion gate:** a removed document is never listed or found, and a repeated purge is harmless. `readable_by` returns only what a person's principals admit, and nothing for `None`.
- **Permission store:** memberships are put, replaced and removed by record; principals are `None` for leavers and unknown users; readers records are put, replaced and forgotten.
- **`source_acl`:** readers copied as written; unknown groups, unknown users and leavers reported; an empty ACL copied and reported.
- **Identity records:** a user record becomes their membership with its version; a leaver keeps their groups; a mismatched, malformed or non-user record is refused. The sync says what changed (`alice: -it-employees`, `olivia left`, `olivia is back`).
- **The T8 step:** every chunk gets the readers and is published, and its readers are recorded. **Publication test (card 4.14):** after T7 and before T8 no one finds the chunk; after T8 only its readers do. A chunk with no sensitivity, no vector or readers already set is refused and nothing is published. If the search store fails, no readers are recorded. **Fast lane:** published chunks get new readers in place and a dropped group loses access. On a page with nothing published, the fast lane only records the readers. Readers first does nothing when they didn't change, closes the old chunks when they did, and skips a new document. A group change rewrites no chunk.
- **Behind T2:** every chunk is published with its page's readers, and only readers find it. Identity records fill the memberships and are never published. Without T7 nothing is published and the event comes back. A group change and a leaver take effect with no chunk rewritten or embedded. The fast lane embeds nothing. **An edit that also revokes closes the old chunks even if the model is down**, then the new version is published when it is back. **Deletion gate:** a purge removes the document and its readers record, and a purged identity record takes its membership with it.
- **Live loop, API, UI and demo:**
  - the inspector loop publishes every chunk it cuts, and follows a group change, a readers change found by the listing, and a delete;
  - the API shows status, readers, versions, unknown groups and who can read what;
  - a deleted page is hidden before the purge, and without T8's stores the view is empty;
  - the UI helpers label statuses and readers;
  - the demo tells the whole story offline and leaves no subscription behind.

## 7. Run it yourself
```powershell
uv run ekb demo t8
```
It needs no model, key or network: the vectors are fake, because what they mean doesn't matter for permissions. You should see:
1. **Initial load:** 12 user records → 12 active memberships, then one line per page (`HR-CMP-01  4 chunks  readers hr-compensation  1 person`) and `48 chunks published from 12 pages; 48 of 48 have readers, a sensitivity and a vector`.
2. **The publication gate:** dana creates `HR-PL-11` for `hr-team`. With the line stopped after T7: 2 chunks, `publishable False`, `0 chunks of HR-PL-11` in the store, bob finds nothing, and publishing them anyway is `refused: … missing readers (T8)`. T8 resumes: `bob, dana, lucia` find it; alice doesn't.
3. **Who can read what:** a grid of the twelve users against the thirteen pages.
4. **A group change:** alice removed from `it-employees` → `lost HR-PL-02, HR-PL-03, HR-PL-08`, `Chunks rewritten: 0. Texts embedded: 0.`
5. **A leaver:** olivia deactivated → still in `all-staff, ca-employees`, finds `0 chunks (was 25)`.
6. **The fast lane:** lucia removes `hr-team` from `HR-PL-05` → `T2 acl_only -> T8 fast lane`, `readers -hr-team on 4 published chunks`, `After: carla, javier, lucia`, `Texts embedded: 0`.
7. **A delete:** dana deletes `HR-PL-99` → `4 chunks out of the search store`, no one finds it, readers record gone.
8. **Summary:** `T2 decided 29 events: process 27, acl_only 1, skip 0, purge 1`, and every chunk in the store publishable.

In the inspector: `uv run ekb inspector --header none --embed fake` (or with your models), then `npm run dev` in `ui/`, and open **T8 Permissions**. Try these in the mocks' `/docs` pages:
- remove someone from a group in the identity provider: their row in the grid changes, no document does;
- deactivate someone: their row turns empty and struck through;
- change a page's readers: the page's readers version moves past its text version ("changed by the fast lane");
- delete a page: it disappears from the tab.

## 8. Decisions and limits
- **Settled in S11's plan (25 November 2026):**
  1. Groups stay on the chunks, and a person is expanded to their groups at query time (D8: "user and group IDs"). A group change rewrites nothing.
  2. Memberships come from the identity provider's per-user events through the change log, like the entity store in S9; no snapshot of the identity provider.
  3. Inactive or unknown users read nothing, whatever their groups (a gate test).
  4. Unknown groups are kept and reported. An empty ACL is published and reported as readable by no one. Unknown users were already dropped by T1.
  5. T8 requires the sensitivity but does not narrow readers by it; that is Cedar's job in Q2 (S13/S15).
  6. Edits that add readers withdraw the old text until publication. Revocation-only edits narrow access immediately.
  7. The search port is `publish`, `set_readers`, `remove`, `chunks`, `documents`, `readable_by` (filter only; S13 adds ranking). `Chunk` gains `acl_version` and `resolver_id`.
  8. The demo runs offline with fake vectors.
  9. The new T8 tab reports current publication status.
- **The demo runs T8 itself**, right after T7, so it can stop the line between them (card 4.14). The inspector and the tests run it inside `chunk_after_t3(t8=…)`.
- **Copied readers are slightly stale by design** (D8). A page's readers change in the index when its event arrives. The second check against the source, before any text is released, is Q4 (S14).
- **The readers are the same on every chunk of a page.** Per-chunk restrictions (a confidential paragraph inside an all-staff page) are a Cedar policy on the sensitivity, applied at query time.
- **No "list everything alice can see" index.** `readable_by` scans the in-memory store. OpenSearch filters on the fields (S17), and an expanded-users option would make that listing cheap (card 4.13, D8's OpenFGA note).
- **One identity provider.** Merging two (ShopFully and Flipp) into one canonical person is D3's production concern; the mock has one.
- **In memory.** Both stores are lost when the process stops; a rebuild replays the change log (S3). DynamoDB and OpenSearch come in S17.
- **The stores are chosen by hand**, as in earlier steps; the pipeline (S12) reads them from config.

## 9. Check your understanding
1. dana creates a page for `hr-team`. T3 to T7 have run, but T8 hasn't. bob is in `hr-team`. Does he find it? Why is the search store's own check needed when T8 already checks?
2. alice is removed from `it-employees` in the identity provider. Which stores change, how many chunks are rewritten, and how does her next search know?
3. In one save, lucia edits the text of `HR-PL-05` and removes `hr-team` from its readers, while T7's model is down. What can dana find a second later, and why?

<details>
<summary>Answers</summary>

1. No. A chunk without readers cannot be filtered by readers, so it is kept out of the search store altogether until T8 has set them. There is nothing for bob to find. The store checks again because a chunk in the index is a chunk someone can find. If a bug upstream (a new wiring, a missed step) handed it chunks without readers, it refuses the whole batch and keeps the old version, instead of publishing something any filter would treat wrongly.
2. Only the permission store. The identity sync emits an upsert of `identity:users/alice`, T2 processes it, T3 says "not a document", and the membership sync replaces her membership with `{all-staff}`. Zero chunks are rewritten and nothing is embedded: the pages still say `it-employees`. Her next search asks the permission store for her principals (`alice`, `{all-staff}`) and the filter (`admits`) no longer matches the Italian pages.
3. Nothing of `HR-PL-05`, unless she gets it another way (she is not in `es-employees`). T2 decides `process` (the text changed), and `readers_after_t2` calls `readers_first` before T3. The page is already published, so its old chunks get the new readers (`es-employees`) at once. T7 then fails, the event comes back, and the old text stays searchable for `es-employees` until the new version is published. The revocation never waits for a model.
</details>
