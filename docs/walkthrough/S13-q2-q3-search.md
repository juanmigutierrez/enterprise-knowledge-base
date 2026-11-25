# S13 · Q2 + Q3: how retrieval respects who is asking

> Architecture: `docs/architecture.md` (box 6, "Knowledge API … Q2 pre-filter · Q3 hybrid + rerank"; box 5, "Search index … BM25 + vectors + ACL + entity IDs"). Plan: `docs/plan.md` §4.2, S13. Option cards: `docs/option-evaluation.md` 4.5 (retrieval mode), 4.6 (reranking), 4.7 (how many chunks), 4.13 (permission enforcement). Decisions: D5 (keywords, vectors and permission filters in one engine), D8 (source permissions copied into the index as user and group IDs, used as a pre-filter), D9 (the per-request steps Q1 to Q4), D10 (the model ID stored with every vector), D15 (the Q3 reranking row).

## 1. In one paragraph
Until now the knowledge base could only be written to: S12 put every page's chunks in the search store with their readers, and nothing read them back. S13 adds the first half of the query path. **Q2** answers "who is asking?". It takes the person's user ID and returns the principals they read with **right now**: their ID and the groups the identity provider lists for them today. A leaver or an unknown ID gets nothing. **Q3** then ranks only the chunks those principals can read, in two ways. The **keyword leg** (BM25) finds exact words, codes and names. The **vector leg** compares the question's vector with each chunk's and finds paraphrases and other languages. Q3 merges the two lists by rank (**reciprocal rank fusion**) and keeps the top 5. The filter is applied inside the store, before anything is scored. A chunk the asker may not read never gets a score, never counts in the statistics and never takes one of their five places. The same question asked by alice and by bob therefore returns different chunks, and nothing in alice's answer shows that bob's pages exist.

## 2. Where it sits in the flow
```
write path (S12):  … T7 ▶ T8 ─▶ search store (chunks + readers + vectors)   permission store (who is in which group)
                                        ▲                                              ▲
query path (S13):  (user, question) ─▶ Q2 pre-filter ───── principals(user) ───────────┘
                                        │  Scope(user, principals | None, reason)
                                        ▼
                                   Q3 hybrid_rrf
                                     ├ encode question if a model is available
                                     ├ snapshot = store.ranking_snapshot(principals)
                                     ├ keyword leg: snapshot.keyword(question, 20)        BM25
                                     ├ vector leg:  snapshot.nearest(vector, model, 20)   cosine
                                     ├ fuse (RRF)  ▶ rerank (none)  ▶ top 5
                                     ├ check snapshot and current user membership
                                     ▼
                                   Retrieval(hits=[Hit(chunk, rank, score, keyword_rank/score,
                                                       vector_rank/score)], notes)
                                     ▼
                                   Q4 (S14): re-check against the source, redact, cite

input:  ("alice", "How many days of annual leave do I get?")
output: SearchResult(scope=Scope(alice, {all-staff, it-employees}),
                     retrieval=Retrieval(hits=[HR-PL-01#1, HR-PL-09#2, …], searched=36))
```

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/contracts/retrieval.py` | `Scope` (Q2's output), `Leg`, `Hit`, `Retrieval` (Q3's output), `SearchResult`. These are the shapes the independent query steps share |
| `src/ekb/stores/search/port.py` | `ranking_snapshot(principals)` captures the readable corpus after question encoding. Its keyword and vector rankings use the same chunks. If that corpus or the user's membership changes during ranking, the query path discards the result and invites a fresh search. |
| `src/ekb/stores/search/options/in_memory.py` | BM25 (k1 1.2, b 0.75), with statistics over the chunks the asker may read; cosine similarity for chunks embedded by the same model; terms counted once per chunk at publication |
| `src/ekb/stores/search/text.py` | `indexed_text` (header, trail, text: T7's recipe) and `terms` (lower case, accents folded, letters and digits) |
| `src/ekb/access/query/q2_prefilter/{port,options/acl}.py` | `PreFilter`, `MembershipLookup`; `q2_prefilter: acl` |
| `src/ekb/access/query/q3_retrieve/port.py` | `Retriever`, `QueryEncoder`, `Reranker`, `EncoderUnavailableError` |
| `src/ekb/access/query/q3_retrieve/fusion.py` | `fuse` (reciprocal rank fusion), `renumbered` |
| `src/ekb/access/query/q3_retrieve/options/{hybrid_rrf,rerank_none}.py` | `q3_retrieve: hybrid_rrf`; `q3_rerank: none` |
| `src/ekb/app/choices.py` | `choose_query`, `QUERY_STEPS`; stand-ins `q2_prefilter → acl` and `q3_rerank → none` |
| `src/ekb/app/search.py` | `Search` (`ask`), `build_search`, `EmbedderEncoder` (T7's embedder as Q3's encoder), `query_encoder`, `encoder_note` |
| `src/ekb/app/demos/search.py`, `demos/__init__.py` | `ekb demo search` |
| `src/ekb/operate/inspector/{search,models,app}.py`, `src/ekb/app/inspector.py` | `SearchPanel`, `withheld`; `GET /api/search?user=&q=` (`SearchView`, `SearchHitView`, `WithheldView`); the query path wired in the inspector |
| `ui/src/components/SearchScreen.tsx`, `ui/src/search.ts`, `App.tsx`, `api/client.ts`, `styles.css`; `ui/openapi.json`, `src/api/schema.d.ts` | The **Search as user** tab and the regenerated contract |
| `tests/unit/stores/search/test_ranking.py`, `tests/unit/access/query/`, `tests/unit/app/test_search.py`, `test_choices.py`, `test_demos.py`, `tests/unit/operate/inspector/test_search_panel.py`, `tests/contract/test_ports.py`, `ui/tests/search.test.mjs` | See §6 |

## 4. How it works

### Q2: who is asking, now
`q2_prefilter: acl` reads one membership from the permission store. S11 fills that store from the identity provider's user records, through the change log:
```python
membership = self._permissions.membership(user_id)
if membership is None:
    reason = "unknown to the identity provider: reads nothing"
elif not membership.active:
    reason = "inactive (has left): reads nothing, whatever groups the record keeps"
else:
    reason = f"reads as {user_id} + {groups}"
principals = membership.principals if membership is not None else None
```
The result is a `Scope`. Q2 knows nothing about the question. Its only job is to say what this person reads with at this moment. The groups are read on every question, so a group change made in the identity provider applies to the next search. The chunks still carry `hr-compensation`; bob is simply no longer in it. No chunk is rewritten (S11).

### The pre-filter lives in the store
Both ranking methods start the same way: keep the visible chunks that `admits(chunk.acl, principals)`, and rank only those.
```python
def _readable(self, principals: Principals | None) -> tuple[Chunk, ...]:
    if principals is None:
        return ()
    return tuple(c for c in self._visible() if c.acl is not None and admits(c.acl, principals))
```
This is what "pre-filter" means in D8 and card 4.13. A post-filter would rank everything, then drop what the asker can't read. Alice could then get two results instead of five, because the restricted pages took the other places. The ranking itself would also have seen chunks she may not read. With a pre-filter, a restricted chunk can't push one of hers out of the top `k`. OpenSearch does the same with the readers in the query's `filter` clause (S17). `_visible()` also leaves out a document that is hidden during recovery, or whose lifetime a delete has closed (S11, S12). So the deletion gate holds for search as well.

### The keyword leg: BM25
BM25 scores a chunk for each question term it contains:
```
idf(term)   = ln(1 + (N − n + 0.5) / (n + 0.5))         N chunks searched, n of them hold the term
score(term) = idf · tf · (k1 + 1) / (tf + k1 · (1 − b + b · length / average length))
```
- **A rare term counts more.** "Italy" appears in a few chunks and scores high. "the" appears almost everywhere and scores close to zero.
- **Repeating a term helps less and less.** `k1` = 1.2 sets how quickly the extra score fades.
- **A longer chunk is penalized a little** for holding more words (`b` = 0.75).

**Statistics come only from the chunks the asker may read.** `N`, `n` and the average length are counted over the admitted chunks. So a restricted page full of "salary" never moves alice's scores. Otherwise a score could reveal something about pages she cannot see (a test checks this). OpenSearch computes these statistics per shard over every document, so S17 will rank slightly differently.

**What is indexed** is the chunk's context header, its section trail and its text: the same text T7 embeds (`text.indexed_text`; a test checks that it matches T7's recipe). **How text becomes terms:** lower case, accents removed, then every run of letters or digits is a term:
```python
terms("Ley 10/2021, HR-PL-07 (€75)") == ["ley", "10", "2021", "hr", "pl", "07", "75"]
terms("Días de VACACIONES") == ["dias", "de", "vacaciones"]
```
There is no stemming and no stop-word list. So "parental" does not match "parentale", and "what are the … in" matches a little everywhere, with little weight. Closing that gap is the vector leg's job; language analyzers are an option to measure (card 4.5, S17).

### The vector leg: the question in T7's space
A question's vector can only be compared with vectors from the **same model** (D10). `ekb.app.search.query_encoder` therefore does not pick a model of its own. It wraps T7's embedder, the one the worker uses, so the model and the gateway `embed` task are the same:
```python
class EmbedderEncoder:
    @property
    def space(self) -> str:
        return self._inner.model_id  # model@1(input@1, ollama:bge-m3, 1024)

    def encode(self, question: str) -> Sequence[float]:
        try:
            return self._inner.embed([question]).vectors[0]
        except GatewayError as exc:
            raise EncoderUnavailableError(str(exc)) from exc
```
The store compares the question only with chunks whose `embedding_model` equals `space`. It counts the others as `skipped`, and Q3 adds a note, for example "3 of 36 chunks were embedded with another model …". The keyword leg can still find those chunks. A question belongs to no document, so its vector is cached without a document tag. The cache keeps a hash of the text and the vector, never the question itself.

Two cases leave the keyword leg alone, and the retrieval says so in `notes`:
- **Fake vectors** (offline, and the `ci` profile). They are hashes with no meaning, so comparing a question with them ranks chunks at random, and RRF would mix that noise into the top 5. `query_encoder` returns `None`: "keyword leg only: no embedding model for questions".
- **The model is down** at query time. The search still runs on keywords: "keyword leg only: the embedding model did not answer (…)". This never widens what anyone can read, because the pre-filter does not depend on vectors. T7 is different: it fails closed, because a chunk without a vector must not be published.

### Fusion: ranks, not scores
BM25 scores have no upper bound, while cosine similarity stays between −1 and 1, so the two can't be added. Reciprocal rank fusion uses only positions: a chunk at rank `r` in a leg earns `1 / (60 + r)`, and the fused score is the sum over the legs that found it.
```
keyword: #1 HR-PL-01#2   #2 HR-PL-04#1
vector:  #1 HR-PL-02#1   #2 HR-PL-01#2
fused:   HR-PL-01#2 = 1/61 + 1/62 = 0.0325   (found by both: first)
         HR-PL-02#1 = 1/61        = 0.0164
         HR-PL-04#1 = 1/62        = 0.0161
```
A chunk that both legs rank near the top beats a chunk only one leg ranks first. That is the point of hybrid search: words and meaning agree. Ties are broken by the better single rank, then by document and position, so the order is always the same. Each `Hit` keeps both ranks and both scores, so the UI can show why each result is there.

### Rerank, then cut
Each leg proposes up to 20 candidates (`candidates`). After fusion, the reranker reorders every fused candidate, and then the first `top_k` (5, from `config/`) are kept. With `none`, the fused order stays as it is. A real reranker reads the question with each passage and reorders the candidates; card 4.6 will measure whether that lets fewer, better chunks reach the answering model (card 4.7).

### Options from config
`choose_query(settings)` reads Q2, Q3 and Q3's `rerank` setting. The reranker is configured as a setting of Q3, so it is moved out of Q3's settings and chosen as `q3_rerank`, with its own stand-in. The demo and the inspector print the swaps:
```
q2_prefilter acl_and_cedar -> acl (not built yet)
q3_retrieve hybrid_rrf(top_k=5)
q3_rerank bge_reranker -> none (not built yet)
```
`config/default.yaml` is unchanged. `candidates` (20) and `rrf_k` (60) are code defaults until an experiment says otherwise.

### Withheld: an inspector's view, never an agent's
The demo and the inspector also count the chunks that share a word with the question but that the asker may not read (`ekb.operate.inspector.search.withheld`). This shows *why* two people get different answers. Q3 cannot produce this count, because it never sees those chunks. An agent must never learn it either: "a restricted page about salary bands exists" is already a leak. It exists only in tools that show everything to whoever runs them locally (D19).

### The inspector and the UI
`ekb inspector` builds the query path from the profile over the worker's own stores. The questions use the same T7 option the worker runs (`--embed` included). On startup it prints `Q2 + Q3: …`. `GET /api/search?user=alice&q=…` returns the scope, the hits (with both legs' ranks and scores, and the readers that let the asker in), the notes, and what the pre-filter kept out.

The **Search as user** tab has:
- a question box and three suggested questions;
- two columns, each with its own person (active users, leavers marked "has left", and an unknown `mallory`).

In each column:
- the person's reason (`reads as …`, or why they read nothing) and Q3's notes;
- a grey box above the hits with what the pre-filter kept out;
- each hit's legs as a badge (green when both legs found it), its trail, text, readers and scores;
- a mark on any hit the other column's person may not read ("alice can't read this"), using the same rule as the store's filter;
- a fresh answer every 5 seconds, so a revoke you make in the mocks shows up without asking again.

## 5. Options
- **Built now:**
  - `q2_prefilter: acl`, which runs as the stand-in for `acl_and_cedar`;
  - `q3_retrieve: hybrid_rrf` (top_k 5, candidates 20, rrf_k 60);
  - `q3_rerank: none`, which runs as the stand-in for `bge_reranker`, and for `cohere_rerank_3_5` in the bedrock profile;
  - ranking in `search: in_memory`.
- **Later:**
  - `acl_and_cedar` (S15): Cedar policies on top of the readers, such as "the roadmap agent never reads HR data" and sensitivity rules, once an agent identity exists;
  - a real reranker (card 4.6). Ollama serves no reranking models officially, so a local bge-reranker needs its own runtime; Cohere Rerank is the production candidate;
  - `keyword_only` and `vector_only`, to compare with hybrid (card 4.5, S18);
  - OpenSearch with language analyzers (S17).
- **Switching:** `q2_prefilter`, `q3_retrieve` and its `rerank` setting in `config/*.yaml`. Questions follow `t7_embed`.

No defaults changed.

## 6. What the tests prove
- **Pre-filter gate (store):**
  - the best BM25 match is restricted, yet alice never gets it and it does not take her single place;
  - the same holds for the vector leg;
  - `None` principals rank nothing.
- **No side channel:** adding a restricted page full of the query's words leaves alice's BM25 scores exactly as they were.
- **Deletion gate:** neither leg returns a removed document, one hidden during recovery, or one whose lifetime a delete has closed. The hidden one comes back when released.
- **BM25:**
  - rarer and more frequent terms rank higher;
  - chunks with no shared term are not returned;
  - case and accents don't matter;
  - the header and the trail are searched;
  - the score matches the formula;
  - the indexed text is T7's recipe;
  - a new version is ranked by its own words, and the fast lane's new readers apply at once.
- **Vectors:**
  - cosine order is correct;
  - only chunks with the same model ID are compared, and the others (another model, another size) are counted;
  - a zero vector ranks nothing.
- **Q2:**
  - an active person reads with their current groups;
  - a group change is in the next scope;
  - **a leaver and an unknown person read nothing**.
- **Fusion:**
  - a chunk found by both legs beats one found first by a single leg;
  - scores on different scales don't matter;
  - ties are stable;
  - `rrf_k` counts, and a negative one is refused.
- **Q3:**
  - both legs are fused and each hit says where it came from;
  - the same question gives bob the salary bands and alice never;
  - **a scope that reads nothing gets nothing, and neither the store nor the encoder is asked**;
  - without an encoder, or with the model down, only the keyword leg runs and says why;
  - chunks from another model are left to the keyword leg, with a note;
  - the reranker sees every candidate before the cut;
  - `candidates` and `top_k` are applied;
  - an empty question searches nothing;
  - impossible settings are refused.
- **Wiring:**
  - the profiles' stand-ins (local, ci, bedrock);
  - a missing `rerank` setting means `none`;
  - the same question over the worker's stores gives alice and bob different hits;
  - **a group change reaches the next search with no chunk rewritten**;
  - a deleted page and a leaver find nothing;
  - questions use T7's model ID, and fake vectors switch the vector leg off;
  - a model error becomes "keyword leg only".
- **Inspector and UI:**
  - two people get their own hits, and the inspector shows what was kept out, with its readers;
  - a leaver and an unknown person read nothing;
  - **a deleted page is neither a hit nor withheld before the purge**;
  - a user is required;
  - an unwired inspector says so;
  - the UI helpers label legs, scores, trails and what was kept out, and tell whether the other person may read a hit;
  - the contract test covers the three new ports.
- **Demo:**
  - it tells the whole story offline;
  - with a model, both legs run;
  - the CLI takes a question and `--user`.

## 7. Run it yourself
```powershell
uv run ekb demo search
```
It needs no model, key or network. You should see:
1. **The options:** the three lines above and `questions not embedded: t7_embed runs fake vectors, which carry no meaning, so Q3 runs its keyword leg alone.`
2. **Who is asking:** `alice reads as alice + all-staff, it-employees … searches 36 chunks of 9 pages; the other 12 are never scored for alice.` Bob `searches all 48 chunks of 12 pages.`
3. **Three questions, each asked by alice, then bob.** For each one you see the keyword leg's best three (`HR-PL-01#1 16.95 · …`) and the fused top 5, with each hit's ranks and RRF score. Then comes the inspector line, for example `the pre-filter kept out 4 chunks sharing a word with the question, from HR-CMP-01 (4). alice never learns they exist.`
   - On the salary question, bob's top 4 are the salary bands. Alice gets weaker matches from the pages she can read; the first is the annual-leave chunk that mentions Italy.
   - On "Ley 10/2021", alice gets weak matches from the pages she can read. Bob (hr-team) gets the Madrid remote-work agreement first.
4. **A group change:** dana removes bob from `hr-compensation`. Bob's `before` list is the salary bands; his `after` list has none. `Chunks rewritten: 0.`
5. **People who read nothing:** olivia (who has left) and `mallory` (unknown) get `0 hits: nothing was searched`.

Variations:
- `uv run ekb demo search "parental leave" --user carla --user alice`: your own question, asked by the people you name. Offline, both get English pages that say "leave". Carla's Spanish birth-leave page (HR-PL-07) and alice's Italian one (HR-PL-03) share no word with the question, so the keyword leg cannot find them.
- `uv run --extra ollama --env-file .env ekb demo search --models`: questions embedded by bge-m3 (Ollama), so both legs run. Each person then also gets a `vector` line, and hits show `keyword 2 · vector 1`. Try "parental leave": alice should now get HR-PL-03 (congedo parentale) through the vector leg, though it shares no word with the question. If Ollama does not answer, the demo says so and runs offline.

In the inspector:
1. Run `uv run ekb inspector --header none --embed fake` (or `--embed model` with Ollama), then `npm run dev` in `ui/`, and open **Search as user**.
2. Keep alice on the left and bob on the right and ask the salary question. The salary bands appear on bob's side marked "alice can't read this", and the grey box on alice's side shows the 4 chunks of HR-CMP-01 the pre-filter kept out.
3. In the identity mock's `/docs`, remove bob from `hr-compensation`. Within a few seconds his column changes: 44 chunks searched, and the salary bands move into his grey box.
4. Pick `mallory (unknown)` to see “reads nothing”. To try a leaver, deactivate Olivia in the identity mock first and wait for the inspector to sync.

## 8. Decisions and limits
- **Settled in S13's plan (25 November 2026):**
  1. Q2 builds `acl` only. `acl_and_cedar` runs as its stand-in until S15, when an agent identity exists to write policies about.
  2. The reranker port exists with `none` only, and the configured reranker runs as that stand-in. The reranker is decided by card 4.6 (D15's row says so).
  3. With fake vectors the vector leg is switched off and the output says so. `--models` shows real hybrid search, and the tests prove the fusion with hand-made vectors.
  4. Questions are embedded with T7's own embedder and compared only with chunks carrying T7's current model ID. They are cached untagged: the cache keeps a hash and a vector, not the question.
  5. If the embedding model fails at query time, the search falls back to the keyword leg with a note.
  6. BM25 statistics come from the chunks the asker may read.
  7. The tokenizer lowercases, folds accents and keeps words and numbers, with no stemming and no stop words. The indexed text is header + trail + text.
  8. `candidates` = 20 and `rrf_k` = 60 are code defaults; `config/` is unchanged.
  9. The query path is wired in `ekb.app.search`. The shared shapes are in `ekb.contracts.retrieval`.
  10. "Withheld" is in the demo and the inspector only, never in Q3's result.
  11. The UI shows one question, two people and two columns, refreshed every 5 seconds.
- **The user ID is trusted.** Nothing checks who is asking yet. Token checks and acting on behalf of the user come with the auth middleware and MCP (S15). The inspector lets you ask as anyone because it is a local admin tool.
- **Copied permissions are slightly stale by design** (D8). The pre-filter uses the readers T8 last copied and the groups last synced. Q4 (S14) checks again against the source before any text is released.
- **Every question returns up to five chunks**, even nonsense: there is no minimum score. Deciding that nothing answers the question belongs to Q4 and the answering model (card 4.11's refusal rate).
- **No diversity.** The top 5 may all come from one page (bob's salary answer does). Whether to cap chunks per page is a question for card 4.7.
- **In-memory ranking cost.** The store scans the readable chunks to build the ranking snapshot and checks the readable set again before returning hits. This is practical for the demo's 48 chunks. OpenSearch uses its inverted index and approximate nearest neighbours (S17).
- **Questions and documents are embedded the same way.** bge-m3 needs nothing more. Cohere Embed v4 distinguishes `search_query` from `search_document`, which the Bedrock embedding option will have to pass (S17/D10). In the cost log, question embeddings appear under the `embed` task with no document.
- **One language analyzer for all.** Accent folding helps Spanish and Italian a little; real stemming per language is OpenSearch's job (S17).

## 9. Check your understanding
1. Alice asks "salary bands". In the whole store, the best BM25 match is a chunk of HR-CMP-01, which only `hr-compensation` can read. Why can it never appear in her top 5, or push one of her chunks out? Why do her scores not change if HR-CMP-01 is edited to say "salary" fifty times?
2. Dana removes bob from `hr-compensation` in the identity provider. Which store changes? How many chunks are rewritten? How does bob's very next search know?
3. The offline demo says "questions not embedded". Why doesn't Q3 compare the question with the fake vectors anyway? What happens if Ollama stops after the inspector has started? And what if T7 switches from bge-m3 to Cohere Embed v4 and only half the chunks have been embedded again?

<details>
<summary>Answers</summary>

1. The search store filters before it ranks: `keyword()` first keeps the chunks `admits(chunk.acl, alice's principals)`, and alice is not in `hr-compensation`. The HR-CMP-01 chunk is never scored, so it can't take a place, and her five places all go to chunks she may read. A post-filter would rank it and then drop it, which could leave her with fewer results. Her scores don't move because BM25's statistics (how many chunks hold "salary", the average length) are counted over the chunks she may read. HR-CMP-01 is not among them, so its edit changes nothing for her, and her scores can't reveal anything about it.
2. Only the permission store. The identity sync emits bob's user record, the worker's T2 → T3 route reads it, and the membership sync replaces his membership without `hr-compensation`. Zero chunks are rewritten: the salary bands still name `hr-compensation`. Bob's next question goes through Q2, which reads his groups from the permission store at that moment. His principals no longer include `hr-compensation`, so the store's pre-filter no longer admits those chunks.
3. Fake vectors are hashes of the text: two related texts are no closer than two unrelated ones. The vector leg would rank chunks at random, and RRF would put that noise into the top 5 next to the real keyword matches. So `query_encoder` returns no encoder and Q3 runs the keyword leg alone, saying so. If Ollama stops later, `encode` raises `EncoderUnavailableError` and Q3 searches with keywords only, with the note "the embedding model did not answer". Nobody reads more than before, because the filter doesn't depend on vectors. After a switch to Cohere, T7's model ID changes. The vector leg compares the question (embedded by Cohere) only with chunks carrying the new ID. The rest are skipped and counted in a note ("… were embedded with another model"), so a bge-m3 vector is never compared with a Cohere one. Those chunks can still be found by the keyword leg until they are embedded again.
</details>
