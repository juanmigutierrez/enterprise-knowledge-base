# S17c · The search store on OpenSearch

> Architecture: `docs/architecture.md` §2 (box 5: "Search index · OpenSearch, 1 node, same mappings and queries"; the arrow "T8 → publish chunk + vector + resolved ACL"). Plan: `docs/plan.md` §4.2, S17. Option cards: `docs/option-evaluation.md` 4.16 (search store), 4.5 (retrieval mode), 4.13 (permission enforcement). Decisions: D5 (OpenSearch for hybrid search), D8 (readers filtered in the index), D14 (OpenSearch locally too). Earlier: S11 (the port's three rules), S13 (BM25, vectors, the pre-filter, the ranking snapshot).

## 1. In one paragraph
The search store holds the only chunks an agent can ever find. S11 wrote its port with three rules: the publication gate at the door, one version at a time, and deleted is gone. S13 added ranking over the asker's readable chunks only. S17c is the option the design names, **OpenSearch** (D5, D14): one searchable document per staged chunk, the readers as `keyword` fields in every query's filter, BM25 on the header + trail + text, and one cosine `knn_vector` field per vector size. Q2 and Q3 don't change: they ask the same port, and RRF still fuses the two legs in Python. One rule differs on purpose and is written down, not hidden: **OpenSearch counts BM25's statistics over the whole index**, not over what the asker may read.

## 2. Where it sits in the flow
```
T8 publish(document, chunks) ─▶ check_batch (the gate) ─▶ stage chunks + switch active generation ─▶ ekb-chunks
T2 fast lane set_readers ──────▶ the same chunks, new readers ─────────────────────────────▶ ekb-chunks
purge remove(document) ────────▶ delete by query ──────────────────────────────────────────▶ ekb-chunks
worker recovery set_visible ───▶ ekb-chunks-hidden

Q3: ranking_snapshot(principals) ─▶ readable chunks (readers filter, minus hidden and closed)
        ├─ keyword: match on `indexed`, filtered to the snapshot's IDs ─▶ BM25 scores
        └─ nearest: knn on vector_<size>, filtered to the snapshot's IDs ─▶ cosine
```

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/stores/search/options/opensearch.py` | `OpenSearchSearchStore` (`search: opensearch`), the index mappings, the readers filter, the snapshot |
| `src/ekb/stores/search/rules.py` | `check_batch` (the publication gate) and `best` (the tie-break), shared by both options |
| `src/ekb/stores/search/options/in_memory.py` | Uses `rules.py`; nothing else changed |
| `tests/contract/test_search_store.py` | The port's rules on `in_memory`, OpenSearch over a fake client, and real OpenSearch |
| `tests/fakes/opensearch.py` | `FakeOpenSearch`: the part of the query language the store uses, with a strict mapping |
| `pyproject.toml` | Only this option imports opensearch-py (an import-linter contract) |

## 4. How it works
Each staged chunk in `ekb-chunks` has a physical ID unique to its publication. The logical chunk ID stays in the payload. The `ekb-chunks-active` index records each document's active generation.

| field | why |
|---|---|
| `generation` | identifies the complete publication containing this chunk |
| `acl.users`, `acl.groups` | the readers, `keyword`, filtered on in every query |
| `document_key`, `document_version`, `original_lifetime`, `position` | which document, version and lifetime; the sort order |
| `indexed` | header + section trail + text (`indexed_text`, as T7 embeds it), analyzed by `ekb_text` |
| `vector_<size>` | the vector, `knn_vector`, cosine, Lucene's HNSW |
| `embedding_model` | which model made it |
| `chunk` | the whole chunk as JSON, not indexed: read back unchanged |

**The pre-filter is in every query** (D8, card 4.13). `admits` from `ekb.contracts` becomes a filter clause:

```python
should = [{"term": {"acl.users": principals.user_id}}]
if principals.groups:
    should.append({"terms": {"acl.groups": sorted(principals.groups)}})
return {"bool": {"should": should, "minimum_should_match": 1}}
```

A filter clause decides which documents exist for the query: a chunk the asker may not read is never scored and never takes one of the `k` places. The store pages through matching chunks and hidden items, then checks each candidate document's lifetime in the acceptance log (S17a). Only visible chunks from the current lifetime enter the ranking snapshot.

**One version at a time.** The worker writes the replacement chunks under fresh physical IDs and refreshes them. Once every chunk succeeds, it switches one document in the active-generation index. Readers select that generation and retry a paged scan if the selection changes midway. A failed stage leaves the previous version readable. After a successful switch the worker deletes obsolete and abandoned chunks.

**Both legs rank the same snapshot.** `ranking_snapshot` reads the asker's readable chunks once. Each leg then queries OpenSearch with an `ids` filter of exactly those chunks: `match` on `indexed` for BM25, `knn` on `vector_<size>` for vectors. `is_current()` reads the readable chunks again and compares; Q3 discards a ranking whose corpus changed underneath it (S13).

**Vectors of several sizes.** A `knn_vector` field has one dimension, and the port promises that chunks from another model or of another size are skipped and counted, not compared. So the store adds one field per size on first use (`vector_32` for fake vectors, `vector_1024` for bge-m3), and the vector leg only asks chunks of the same model ID and size. Lucene's cosine score is `(1 + cos) / 2`; the store turns it back into the cosine the port promises.

**Terms are cut as in memory.** The `ekb_text` analyzer is a `pattern` tokenizer on anything that is not a letter or a digit, then `lowercase` and `asciifolding`: `Días` and `dias` are one term, `HR-PL-07` is three, and there is no stemming.

## 5. Options
| | In memory (default) | `--docker` (local profile) | Later |
|---|---|---|---|
| `search` | `in_memory` | `opensearch` | OpenSearch Serverless (D5), language analyzers (card 4.5), S3 Vectors as a cold tier |

The reranker (card 4.6) and RRF stay in Q3, unchanged; OpenSearch's own hybrid query is not used, so Q3's logic is the same on both stores.

## 6. What the tests prove
On `in_memory`, on OpenSearch over the fake, and on real OpenSearch with `-m integration`:
- **The publication gate**: one unpublishable chunk refuses the whole batch and changes nothing; chunks of another document, two versions, two lifetimes or one ID twice are refused.
- **One version at a time**: a shorter new version leaves none of the old chunks; publishing nothing removes the document.
- **Deleted is gone**, and the deletion gate for ranking: nothing returns a removed, hidden or closed document, in either leg; a hidden one is still there for the worker's update, a closed one not even then.
- **The pre-filter**: `readable_by` returns only what the principals admit (by name or group); neither leg returns a chunk the asker may not read, nor gives it one of the `k` places; `None` reads nothing.
- BM25 finds words whatever their case or accents, in the header and the trail too; the vector leg ranks by cosine and compares only vectors of the same model and size.
- New readers apply at once; a snapshot knows when the readable corpus changed.

On OpenSearch only (the fake): a reader sees the complete previous version during staging and after a failed bulk write; OpenSearch that can't be reached is an `InfrastructureError`; one vector field per size, and a zero vector is kept but not indexed.

In memory only (`tests/unit/stores/search/test_ranking.py`): BM25's statistics come from the readable chunks only.

## 7. Run it yourself
```powershell
make up
uv run ekb demo search --docker
uv run ekb demo search --docker --user alice --user bob "What are the salary bands?"
uv run pytest -m integration tests/contract/test_search_store.py
```
The story is S13's: the same questions for alice and bob, answered from OpenSearch. To look inside while it runs: `curl "http://localhost:9200/_cat/indices?v"` lists the run's `demo-…-ekb-chunks` index, and `curl "http://localhost:9200/demo-…-ekb-chunks/_mapping?pretty"` shows the readers as `keyword` fields and `vector_32` as a `knn_vector`.

## 8. Decisions and limits
- **Settled in S17's plan (26 November 2026):**
  1. OpenSearch's BM25 is used as it is, with the readers in the filter clause. The in-memory store's stricter rule (statistics from the readable chunks only) stays a test of that store alone, and the difference is written down here.
  2. One `knn_vector` field per vector size, plus the model ID, so "skipped and counted" holds; the analyzer is lower case and ASCII folding, no stemming.
- **Settled while building:**
  - A separate active-generation index provides the publication switch. ACL updates also publish a complete generation.
  - The readable snapshot is read whole (with the vectors), then the legs filter on its IDs. That is fine for a few thousand chunks, and it makes `is_current()` check both the physical IDs and the readable chunks.
  - Hidden items have their own small index, for the same reason as in S17b: a flag on the chunks would be lost when the worker publishes them again.
- **The side channel BM25 leaves.** A restricted page full of "salary" lowers how rare "salary" looks for everyone, so it can change the *order* of alice's results, never their content. Scores stay inside Q3 (RRF uses ranks, answers carry no scores). Closing it would mean re-scoring in Python with readable-only statistics, or an index per audience.
- **Every read asks for lifetimes and hidden items** (paged searches plus one acceptance-log read per candidate document). At scale, the change log would mark a closed lifetime on the chunks instead of the store asking.
- The integration suite exercises publication, ACL filtering, keyword ranking and vector retrieval against the Compose OpenSearch node.

## 9. Check your understanding
1. Bob is in `hr-compensation`; alice is not. Both ask for "salary bands" with `k = 1`. Show how the readers filter keeps HR-CMP-01 out of alice's one place, and explain why the same filter applied *after* ranking would not be enough.
2. A page goes from four chunks to two in a new version. Describe the staging write and the generation switch, and why a reader can't see two new chunks next to two old ones.
3. Alice's results come back in a different order on OpenSearch than in memory, though the same pages are cited. What explains that, and why is it not a permission leak?

<details>
<summary>Answers</summary>

1. The filter clause `acl.users: alice` or `acl.groups: [all-staff, it-employees]` decides which documents the query sees at all. HR-CMP-01's chunks carry `acl.groups: [hr-compensation]`, so for alice they don't exist: they aren't scored, and the one place goes to the best chunk she may read. For bob the same clause admits them. A post-filter would rank everything, take the top `k`, then drop what alice can't read. She could get zero results instead of one, and the ranking would have used chunks she may not read.
2. The worker stages two chunks under fresh IDs while readers can still see all four old chunks. It checks the bulk response, switches the active generation with one index write, then removes the old chunks. A failed stage leaves all four old chunks readable.
3. BM25 weighs a term by how rare it is. OpenSearch counts that over every chunk in the shard, restricted ones included, while the in-memory store counts it over alice's readable chunks only. So a word that is common on pages alice can't read weighs a little less in her query on OpenSearch, and her ranks can shift. What she receives is still only chunks the filter admitted, checked again by Q4. Order is all that differs, not who may read what. RRF then fuses ranks, not scores, and no score reaches the agent.
</details>
