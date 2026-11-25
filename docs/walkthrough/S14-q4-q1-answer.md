# S14 · Q4 + Q1: why a second permission check exists

> Architecture: `docs/architecture.md` (box 6, "Knowledge API … Q1 plan · Q4 re-check, redact, cite", and the "current access check" arrow from the Knowledge API back to the connector). Plan: `docs/plan.md` §4.2, S14. Option cards: `docs/option-evaluation.md` 4.8 (query planning), 4.13 (permission enforcement). Decisions: D8 (point 3: check every contributing document before releasing text; a denial, deletion, timeout or unknown result excludes it), D9 (the per-request steps Q1 to Q4), D16 #10 (cached answers, for S15).

## 1. In one paragraph
S13 made retrieval respect who is asking, but it works from **copies**. The readers on each chunk were copied by T8 from the source. The person's groups were copied by the identity sync. Copies always lag a little (D8). A page whose readers were narrowed a minute ago, a person just removed from a group, someone who has just left, a page just deleted: all of these still look readable until the worker catches up. **Q4** closes that gap. Before any text leaves the knowledge base, it asks the systems the text came from: the identity provider says who this person is *now*, and the docs tool says who may read each page *now*. Only a clear yes releases a passage. A denial, a deletion, a timeout or an unknown source holds it back, and nothing about a held-back page reaches the asker. What is released has its high-risk personal data removed (IBANs, card numbers, national IDs) and gets a numbered citation. **Q1** runs before the search. It splits a question with several parts into one search per part, so each part gets its own top results before they are merged.

## 2. Where it sits in the flow
```
(user, question)
   ▼
Q1 plan (rules)            "How many days of leave? Can I work remotely?"
   │                         → searches: ["How many days of leave?", "Can I work remotely?"]
   ▼
Q2 + Q3 for each search    (S13: copied readers and copied groups, BM25 + vectors, RRF)
   │
   ▼
merge by rank (RRF over the searches), top 5
   │
   ▼
Q4 source_recheck ───────── current access check (ekb.connect.access_check)
   │                          ├ identity provider: GET /scim/v2/Users/{user}  (once)
   │                          └ docs tool:        GET /pages/{id}            (once per page)
   ├ allowed     ─▶ remove personal data ─▶ cite [1], [2] …  ─▶ Passage
   └ denied / deleted / unavailable ─▶ held back (Withheld: demo and inspector only)
   ▼
Answer (what an agent gets, S15)            AnswerResult (everything, for the demo and the inspector)
  passages: [Passage(citation, text, redactions)]
  notes:    ["1 passage could not be checked …"]   ← never names a held-back page

input:  ("alice", "How many days can I work remotely?")   HR-PL-04 narrowed to hr-team in the source
output: Answer(passages=[HR-PL-01 …, HR-PL-09 …])           HR-PL-04 held back: denied, not mentioned
```

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/contracts/answer.py` | `QueryPlan` (Q1's output); `AccessVerdict`, `SourceAccess` (the source's answer); `Citation`, `Redaction`, `Passage`, `Withheld`, `Released` (Q4's output); `Answer` (what may leave); `AnswerResult` (everything, for local tools) |
| `src/ekb/access/query/q1_plan/{port,options/none,options/rules}.py` | `Planner`; `q1_plan: none` and `rules` |
| `src/ekb/access/query/q4_postcheck/port.py` | `PostCheck`, `SourceAccessCheck`, `Redactor`, `Redacted` |
| `src/ekb/access/query/q4_postcheck/options/source_recheck.py` | `q4_postcheck: source_recheck` |
| `src/ekb/access/query/q4_postcheck/options/redact_{rules,none}.py` | `q4_redact: rules` and `none` |
| `src/ekb/access/query/q3_retrieve/fusion.py` | `merge`: reciprocal rank fusion over whole searches |
| `src/ekb/connect/access_check.py` | `SourceAccessChecker`: the connector's current access check over the identity provider and the docs tool, 2 s per read |
| `src/ekb/connect/{docs_api,identity_api}.py` | A per-call `timeout` on `get_page` and `get_user` |
| `src/ekb/platform/pii.py` | T5's personal-data patterns, moved from `process/t5_classify/` so Q4 redacts with the same ones (T5 imports them from here) |
| `src/ekb/app/answer.py` | `KnowledgeQuery` (`ask`: Q1 → Q2 + Q3 per search → merge → Q4), `build_query`, `access_check` |
| `src/ekb/app/choices.py` | `QUERY_STEPS` Q1 to Q4; `SETTING_STEPS` (`q3_rerank` and `q4_redact` are settings of Q3 and Q4); stand-in `q1_plan → rules` |
| `src/ekb/app/demos/answer.py` | `ekb demo answer` |
| `src/ekb/operate/inspector/{search,models,app}.py`, `src/ekb/app/inspector.py` | `/api/search` runs Q1 to Q4: the plan, and each hit's verdict, citation, released text and redactions; `LateQuery` wires Q4 once the mocks have started |
| `ui/src/components/SearchScreen.tsx`, `ui/src/search.ts`, `styles.css`, `api/client.ts`; `ui/openapi.json`, `src/api/schema.d.ts` | Citations, held-back hits and redactions on the **Search as user** screen, and the regenerated contract |
| `tests/unit/access/query/q1_plan/`, `q4_postcheck/`, `q3_retrieve/test_fusion.py`, `tests/unit/connect/test_access_check.py`, `tests/unit/app/test_answer.py`, `test_choices.py`, `test_demos.py`, `tests/unit/operate/inspector/test_search_panel.py`, `tests/unit/platform/test_pii.py`, `tests/contract/test_ports.py`, `ui/tests/search.test.mjs` | See §6 |

## 4. How it works

### Q1: one search per part
`q1_plan: rules` cuts the question after every question mark, at semicolons and at line breaks. It drops parts with no letter or digit and searches a repeated part once:
```python
_BOUNDARY = re.compile(r"(?<=\?)|[;\n]")

RulesPlanner().plan("How many days of annual leave do I get? How many days can I work remotely?")
# QueryPlan(searches=("How many days of annual leave do I get?",
#                     "How many days can I work remotely?"),
#           notes=("split into 2 searches, one per part of the question",))
```
Why bother? Searched as one, the top 5 go to whichever part has the stronger words, and the other part may get nothing. Searched as two, each part is ranked on its own. There are at most 3 searches, and the parts beyond the third are searched together with it, so no word of the question is lost. A question with nothing to split is searched as it is. Rules can't translate a question, expand an acronym or split on "and". That needs a model: `rewrite_when_needed` (card 4.8), which runs as `rules` until its experiment.

### Merging the searches
Each search runs Q2 + Q3 exactly as in S13. The query path then merges their rankings with the rule Q3 already uses for its two legs, reciprocal rank fusion, and keeps Q3's `top_k` (5):
```python
scores[key] = scores.get(key, 0.0) + 1 / (rrf_k + hit.rank)  # summed over the searches
```
Each search's first place beats every other search's second place, and a passage several searches found adds up and rises. If the person's groups change between two searches, nothing is released ("group membership changed during search: search again"), as in S13.

### Q4: ask the source, for this person, now
`source_recheck` takes the merged hits and asks the access check about each **document** once, in rank order. Five chunks of one page make one question to the source:
```python
keys = tuple(dict.fromkeys(hit.chunk.document_key for hit in hits))
answers = self._check(scope.user_id, keys)
checked = {key: self._answer_for(key, answers) for key in keys}
for hit in hits:
    access = checked[hit.chunk.document_key]
    if access.verdict.released:  # only ALLOWED
        passages.append(self._passage(len(passages) + 1, hit, access))
    else:
        withheld.append(Withheld(hit=hit, access=access))
```
Unknown is no, everywhere:
- the source does not answer, answers with an error or sends a body that doesn't parse: `unavailable`;
- the page is gone: `deleted`;
- the readers or the identity provider say no: `denied`;
- a missing or mismatched reply holds back the affected document. A check that raises holds back the entire batch.

Q4 only narrows. It checks what Q3 found and never adds a page, and nothing is fetched to replace what was held back: the answer has fewer passages.

### The current access check lives with the connectors
The architecture draws an arrow from the Knowledge API back to the connector. Q4 defines the port (`SourceAccessCheck`), and `ekb.connect.access_check.SourceAccessChecker` implements it. `ekb.app` wires them. The query steps never import the connectors (import-linter). For the docs tool, the check makes two reads:
```python
user = self._identity.get_user(user_id, timeout=self._timeout)  # once per question
...
fetched = self._docs.get_page(page_id, timeout=self._timeout)  # once per page
if fetched is None:
    return self._answer(key, AccessVerdict.DELETED, "the docs tool no longer has the page")
if not admits(acl_of(page.acl), principals):
    reason = f"the docs tool's readers no longer include {principals.user_id}"
    return self._answer(key, AccessVerdict.DENIED, reason, version=version)
```
- **The person comes from the identity provider, not the permission store.** The store is the identity sync's copy. Reading the provider means a group removal or a departure counts at once, before any sync.
- **The rule is the same one used everywhere:** `admits(acl, principals)` (S11).
- **Each read uses a 2-second HTTP timeout for each network operation**, not the 10 seconds a connector syncing in the background may wait.
- **A document from a source this check does not cover is `unavailable`.** The search store holds only docs-tool pages today.

In production, this check is a read of the source under the user's own identity (on-behalf-of, S15), or an authoritative permission API that accounts for current group membership (D8). The HR mock exposes its permissions directly, which D8 allows.

### Silent about what was held back
`Answer`, the only thing that leaves, holds no title, citation, text or count for a denied or deleted page. A held-back page is as invisible as one the pre-filter kept out, because "a page about salary bands exists" is already a leak. The one thing an agent is told is how many passages could not be *checked*, without names:
```
note: 5 passages could not be checked against the source, which did not answer: the answer may be incomplete
```
So the agent can say "try again later" instead of "there is no such policy". The withheld hits and every source answer (`Released.withheld`, `Released.checked`) are kept for the demo and the inspector, which show everything to whoever runs them locally (D19).

### Remove personal data, then cite
`q4_redact: rules` removes the high-risk kinds of `PiiKind` from each released passage's text, section trail and title: IBANs, payment card numbers, Italian fiscal codes and Spanish DNI / NIE numbers. Each becomes a marker, and the passage lists what was removed (kind and count, never the value):
```
The March salary of the example employee was sent again to IBAN [IBAN removed] after the first transfer failed.
removed: 1 IBAN
```
Email addresses and phone numbers stay. An HR page giving the payroll team's contacts must stay usable, and T5 doesn't raise a chunk's sensitivity for them either. The patterns are T5's own, moved to `ekb.platform.pii`, so Q4 removes exactly what T5 flags, check digits included: a salary figure is never taken for an IBAN. The index keeps the text as it is; only what is released changes.

Each passage gets a `Citation`: its number (1, 2, 3 … in rank order, with no gaps), the document key, the title the source gave just now, the section trail, the chunk ID and the version of the text released. `label` writes it as `[1] HR-PL-01 · Annual leave policy > How much leave you get`, without repeating a first heading that equals the title. When the source's content hash differs from the one the chunk was indexed from, the citation is marked `source_changed` and a note says the passage may be out of date. Q4 permits this for edits within the same page lifetime. The docs tool returns the first revision of the current lifetime on every page read. If the indexed revision predates it, the ID belongs to a replacement page and Q4 withholds the deleted text. Missing lifetime information also holds the passage back. Only citation numbers reach the agent. Q3 ranks, scores, corpus counts and provider error details stay in diagnostics. A local membership or readable-index change during source checks discards the pending answer.

### The inspector and the UI
`ekb inspector` builds the same path. Q4 checks against the mocks the inspector starts; `LateQuery` connects it once their addresses are known. `GET /api/search` now returns:
- the plan: `searches` and `planner_id`;
- `postcheck_id`;
- `released` and `held_back`;
- for every hit: its `verdict` (the source's own reason in `access_reason`), its `citation`, the `released_text` with markers, its `redactions` and `source_changed`.

On the **Search as user** screen:
- each column shows Q1's searches and "Q4: 3 cited, 2 held back";
- a cited hit has a green `[n] cited` badge and the released text, with removed data highlighted;
- a held-back hit is dashed and has a red `held back: denied` badge, the source's reason and the indexed text greyed out. That is the inspector's view; an agent never sees it.

## 5. Options
- **Built now:**
  - `q1_plan`: `none` and `rules`. `rules` runs as the stand-in for `rewrite_when_needed` (local and bedrock profiles); `ci` keeps `none`.
  - `q4_postcheck: source_recheck`, over the connector's `SourceAccessChecker` (`source@1(hr_docs, timeout=2s)`).
  - `q4_redact`: `rules` (the default when Q4 has no `redact` setting) and `none`.
- **Later:**
  - `rewrite_when_needed` (card 4.8): the gateway's `plan_query` model rewrites multi-part and non-English questions.
  - A route for counting questions to SQL (S14b).
  - Presidio for redaction, which also finds names.
  - The answer cache with a re-check on reuse (D16 #10) and the on-behalf-of source read, both with the Knowledge API (S15).
  - The other baselines of card 4.13 (pre-filter only, post-filter only) as experiment options (S18).
- **Switching:** `q1_plan` and `q4_postcheck` in `config/*.yaml`; the redactor is Q4's `redact` setting (`q4_postcheck: { option: source_recheck, params: { redact: none } }`).

No defaults changed.

## 6. What the tests prove
- **Gates (Q4):**
  - only `allowed` releases. `denied`, `deleted` and `unavailable` hold the passage back;
  - so does an answer that is missing or for another document, and every passage when the check raises;
  - **nothing about a held-back page appears in what the asker sees**: no key, title or text;
  - a person who reads nothing, or no hits, asks the source nothing;
  - each document is asked about once, for the person asking;
  - citations are numbered in rank order with no gaps.
- **Card 4.13 acceptance, through the whole path over the worker's stores and the real mocks:**
  - readers narrowed in the source while the index is stale: Q3 still finds the page, the answer neither cites nor names it, and bob (still a reader) gets it cited. After the sync, the pre-filter alone keeps it out;
  - the docs tool times out: nothing is released and the answer says 5 passages could not be checked. When it answers again, the same person gets the policy;
  - bob removed from `hr-compensation` and olivia deactivated, neither synced: the salary bands and olivia's pages are held back although Q2's copies still admit them;
  - a page deleted in the source before the purge: held back as deleted.
- **The access check against the mocks:**
  - allowed through a group or by name, with the current title, version and hash;
  - denied after a narrowing or a group removal, and for a leaver or an unknown person;
  - deleted on 404;
  - unavailable for a source it doesn't cover, on a timeout, an error status or a body that doesn't parse, from either system;
  - it uses its own timeout, and refuses a timeout of 0.
- **Redaction:**
  - each high-risk kind is replaced by its marker and counted;
  - emails, phone numbers and salary figures stay;
  - two IBANs are counted twice;
  - the text, the trail and the title are all redacted, the index keeps the original, and `none` keeps everything.
- **`source_changed`:** marked only when both hashes are known and differ, with a note naming only the citation's number.
- **Q1:**
  - question marks, semicolons and line breaks split a question;
  - spaces are trimmed, repeats and empty parts dropped, Spanish `¿…?` handled;
  - parts beyond the limit are searched together, and a limit of 0 is refused;
  - `none` never splits.
- **Merge:**
  - a passage both searches found rises;
  - each search's first place beats another's second;
  - a merged hit keeps the legs of its best search;
  - one search keeps its order, and a negative `rrf_k` is refused.
- **Wiring:**
  - the profiles' choices for Q1 to Q4;
  - `redact` is taken out of Q4's settings, and an unbuilt redactor stops with an error;
  - a membership change between two searches releases nothing;
  - the contract test covers `Planner`, `PostCheck`, `Redactor` and the connector's `SourceAccessCheck`.
- **Inspector and UI:**
  - each hit has its verdict, the source's reason, its citation and its released text;
  - the indexed text keeps the IBAN while the released text doesn't;
  - notes never name a held-back page;
  - the UI helpers label the plan, the verdicts and the redactions, and mark the removed parts.
- **Demo:** the whole story runs offline, your own question can be asked as the people you name, and the CLI reports an unreachable mock.

## 7. Run it yourself
```powershell
uv run ekb demo answer
```
It needs no model, key or network. You should see:
1. **The options:** Q1 to Q4, including `q1_plan rewrite_when_needed -> rules (not built yet)`, `q4_postcheck source_recheck`, `q4_redact rules`, and the access check `source@1(hr_docs, timeout=2s)`.
2. **A cited answer:** alice's five passages, `[1] HR-PL-01 · Annual leave policy > How much leave you get` first, after `Q4 asked the source about 2 pages: 2 allowed`.
3. **Two parts:** `Q1 2 searches: …`, and passages from both the leave and the remote-work pages.
4. **Why a second check**, before the worker catches up:
   - HR-PL-04 narrowed to hr-team: alice's answer has 2 passages. The inspector line reads `Q4 held back HR-PL-04 (3 chunks): denied, the docs tool's readers no longer include alice.` Bob gets `[1] HR-PL-04 · Remote work – Milan office > The rule`;
   - bob removed from `hr-compensation`: the salary bands are held back. Olivia deactivated: `answer 0 passages`;
   - HR-PL-99 deleted: `1 allowed, 1 deleted`.
5. **The docs tool not answering:** `answer 0 passages` and `5 passages could not be checked against the source …`.
6. **Personal data:** bob's payroll passage reads `… sent again to IBAN [IBAN removed] after …`, `removed: 1 IBAN`. The `Who to ask` passage keeps the email address and the phone number.

Variations:
- `uv run ekb demo answer "salary bands" --user alice --user bob`: your own question. Bob's answer cites HR-CMP-01; alice's never mentions it.
- `uv run --extra ollama --env-file .env ekb demo answer --models`: questions embedded by bge-m3, as in `ekb demo search`.

In the inspector:
1. Run `uv run ekb inspector --header none --embed fake --interval 60`, then `npm run dev` in `ui/`, and open **Search as user**. The long interval keeps the worker from syncing for up to a minute, so you can watch Q4 work on stale copies (the default is 3 seconds).
2. Ask `How many days can I work remotely?` with alice on the left and bob on the right.
3. In the docs mock's `/docs`, set HR-PL-04's readers to `{"groups": ["hr-team"]}`. Within 5 seconds, alice's HR-PL-04 hits turn into dashed `held back: denied` cards, because Q4 asks the source on every refresh. Bob's stay cited. At the next sync round, they leave alice's column entirely and move to her grey pre-filter box.

## 8. Decisions and limits
- **Settled in S14's plan (25 November 2026):**
  1. Q4 checks against the page's current readers in the docs tool and the person's status and groups read live from the identity provider, not the synced permission store: one identity read per question, one page read per document.
  2. Q4 defines the check's port, and the connect layer implements it (`ekb.connect.access_check`), wired in `ekb.app`. A source without an access check is held back.
  3. A page edited since indexing, with access confirmed, is released, and its citation is marked "source changed since indexing".
  4. Denied and deleted pages are dropped silently. A source that did not answer is reported only as a count of passages, without names. The demo and the inspector show everything held back, with the reason.
  5. Nothing is fetched to refill the top 5 after a passage is held back.
  6. Removed for everyone: IBANs, payment card numbers, Italian fiscal codes and Spanish DNI / NIE numbers, each as a marker. Kept: emails and phone numbers. Context headers are never released.
  7. The personal-data patterns moved unchanged to `ekb.platform.pii`. T5 imports them from there.
  8. `rules` splits on `?` and `;` (and line breaks) into at most 3 searches, merged by reciprocal rank fusion into one top 5. No route to SQL yet (S14b).
  9. No generated answer and no answer cache in S14: it returns the cited passages, which is the Knowledge API's response. Answers and the cache with a re-check on reuse come with the HR assistant (S15). The full card 4.13 test, cached path included, runs in S16.
  10. The Search as user screen is extended, not replaced.
  11. 2 seconds per source read, as a code default; the reads run one after another.
- **The user ID is still trusted.** Token checks and acting on behalf of the user come with the auth middleware (S15). Until then, a source read "as the user" is the mock's permissions read directly.
- **Coverage drops while a source is down**, by design (D8's trade-off): unknown is no. With several sources, one down source holds back only its own pages.
- **One read per page, every question.** Fine at this size. At production volume the reads would run in parallel, and a short-lived per-request cache would be keyed by user, document and version. It would never outlive the request.
- **Redaction is by pattern.** Names, addresses and dates of birth are not found (Presidio, later). The rules apply to every asker, so an HR officer sees the marker too. Showing personal data to the people allowed to see it would be a Cedar policy (S15).
- **Q1's rules are punctuation, not language.** "Leave and remote work" stays one search; translation and acronym expansion need the model option.
- **Fewer than five passages** is a normal answer after Q4 holds some back. Card 4.7 will measure whether to ask Q3 for more candidates.

## 9. Check your understanding
1. Dana narrows HR-PL-04's readers to hr-team in the docs tool, and the worker has not run yet. Alice asks "How many days can I work remotely?". Which steps still find HR-PL-04, and why? Which step stops it, using what? What does alice's answer say about it?
2. Bob was removed from `hr-compensation` in the identity provider a second ago. Q2 still gives him `hr-compensation`, but Q4 holds the salary bands back. Where does each step read bob's groups from? Why does Q4 not use the permission store too?
3. The docs tool stops answering. What does Q4 release, and what does the agent's answer say? Why does it say anything at all, when it is silent about a denied page? And why is a passage from a page edited since indexing released, but one from a page that cannot be checked is not?

<details>
<summary>Answers</summary>

1. Q2 and Q3 still find it. Q2 reads alice's groups (all-staff, it-employees), and the chunks still carry the readers T8 last copied, `all-staff`, so the store's pre-filter admits them and BM25 ranks them. Q4 stops it. It reads alice from the identity provider and HR-PL-04 from the docs tool (`GET /pages/HR-PL-04`), and `admits(page readers now, alice now)` is false, so the verdict is `denied` and the three chunks are held back. Alice's answer says nothing about it: no citation, title or count. She gets the other passages, renumbered from [1]. Only the inspector and the demo show "held back HR-PL-04: denied". After the worker syncs, the fast lane rewrites the readers and the pre-filter keeps the page out on its own.
2. Q2 reads the permission store, the identity sync's copy, which still lists the group until the next sync. Q4 reads the identity provider itself (`GET /scim/v2/Users/bob`), which already lists him without it. Using the store in Q4 would repeat Q2's check with the same stale copy and catch nothing. The point of a second check is to ask the authority, not a copy, because copied permissions are always slightly behind (D8).
3. Nothing from that source: every passage is `unavailable` and held back, because unknown is no. The answer has no passages and the note "5 passages could not be checked against the source, which did not answer: the answer may be incomplete". It says so because "could not check" is a fact about the system, not about the pages: it names none of them. Without it, the agent would tell the person there is no such policy, which is wrong. A denial is different: saying "1 page was denied" would tell the person that something they may not read exists. An edited page is released because access was confirmed now. Only the content is behind, and the worker is about to replace it, so the citation is marked "source changed since indexing". A page that cannot be checked might no longer be readable at all, and releasing it on a guess is exactly the leak Q4 exists to prevent.
</details>
