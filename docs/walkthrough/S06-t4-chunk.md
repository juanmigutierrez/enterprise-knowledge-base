# S06 · T4 Chunk: from parsed blocks to the passages that will be searched

> Architecture: `docs/architecture.md` (box 4, "T4 · Chunk + context header"; transformations table: "Text → chunks with a context header"). Plan: `docs/plan.md` §4.2, S6. Option cards: `docs/option-evaluation.md` 4.2 (chunking: "section boundaries usually beat fixed windows; chunk count multiplies every later cost") and 4.3 (context header, which comes in S8). Related decisions: D15 (the header's model), D16 #4 (one model call per document, for S8).

## 1. In one paragraph
T3 (S5) turns a document into blocks, each knowing the headings above it. T4 cuts those blocks into **chunks**: the passages that T5 labels, T6 links to entities, T7 embeds, T8 gives permissions, and a search finally returns. Where the cuts fall decides what a search can find. A chunk that mixes two topics matches both badly, and a table cut in half loses the column names its numbers depend on. So the chunker built now, `structure_aware`, cuts **along sections**: each chunk stays within one section, and long sections split between whole blocks. Next to it is `fixed`, the baseline that cuts every N tokens regardless of structure, so card 4.2 can measure what structure is worth. The **context header**, a line that tells each chunk which policy and section it is from, is left empty: a model writes it in S8, through the model gateway built in S7.

## 2. Where it sits in the flow
```
T2 (S4) ── process ──▶ T3 (S5) ──▶ [ T4 ]
                                      1. chunker.chunk(parsed)       the option: structure_aware, fixed
                                      2. check(parsed, chunks)       the rules every option keeps
                                   ──▶ T5 … T8 (later steps)

input:  ParsedDocument(document_key, version, title, blocks[kind, text, section, …])
output: ChunkResult(document, chunks, chunker_id, max_tokens, reason)
        Chunk(document_key, document_version, chunk_id, text, section, position, tokens,
              chunker_id, context_header="", department=None, sensitivity=None,
              entity_ids=(), acl=None, embedding_model=None, vector=None)
```
T4 runs right after T3, on the same document, so only where T2 decided `process`. `skip` and `acl_only` never reach it: the text didn't change, so the chunks wouldn't either. `purge` makes whoever keeps the chunks drop them. A structured record (T3's "not a document") is never chunked.

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/contracts/knowledge.py` | `Chunk` gains `section`, `position`, `tokens` and `chunker_id`; its docstring says who fills in each field and that `chunk_id` is `<document_key>#<position>` |
| `src/ekb/contracts/parsed.py`, `__init__.py` | `render_blocks()`: blocks as clean text. `ParsedDocument.text` and T4 both use it, so a chunk's text is written exactly as in the whole document |
| `src/ekb/process/t4_chunk/tokens.py` | `count_tokens()`, `token_spans()`: the approximate token counter (a word or a punctuation mark) |
| `src/ekb/process/t4_chunk/port.py` | `Chunker` (`chunker_id`, `max_tokens`, `chunk(document)`), `chunk_id()`, `new_chunk()`, `positive()` |
| `src/ekb/process/t4_chunk/options/structure_aware.py` | `structure_aware` option: `sections()` groups blocks by section, `_fit()` splits a long section between blocks |
| `src/ekb/process/t4_chunk/options/fixed.py` | `fixed` option: windows of `max_tokens` with `overlap_tokens` shared, the baseline of card 4.2 |
| `src/ekb/process/t4_chunk/step.py` | `ChunkStep.handle(document)`, `ChunkResult`, `summary()`, and `check()`, which holds every chunker to the port's rules |
| `src/ekb/app/chunk.py` | `chunker(name, **settings)`, `chunk_step()`, `Processed`, `chunk_after_t3()` (T3's downstream until S12) |
| `src/ekb/app/inspector.py` | The sync loop runs T4 after T3 and keeps the chunks in a `ChunkStore`; a purge drops them |
| `src/ekb/app/demos/t4.py`, `demos/__init__.py` | `ekb demo t4` |
| `src/ekb/operate/inspector/{log,models,app}.py` | `ChunkStore`, `ChunkEntry`; `ChunkSummaryView`, `ChunkView`, …; `GET /api/t4/chunks`, `GET /api/t4/chunks/document` |
| `ui/src/components/ChunkScreen.tsx`, `chunk.ts`, `App.tsx`, `styles.css`, `api/client.ts` | **T4 Chunk** tab |
| `ui/openapi.json`, `ui/src/api/schema.d.ts`, `ui/package.json` | Regenerated contract; the new UI test file added to `npm test` |
| `tests/unit/process/t4_chunk/test_tokens.py`, `test_structure_aware.py`, `test_fixed.py`, `test_step.py`, `conftest.py` | The counter, both options, the step's checks; every synthetic page |
| `tests/unit/app/test_chunk.py` | T4 behind T3 and T2: what is chunked, what is forgotten, what happens when T4 fails, the deletion gate through the API |
| `tests/unit/operate/inspector/test_inspector.py`, `tests/unit/app/test_inspector_command.py`, `test_demos.py`, `test_parse.py` | The endpoints and the purge rule; T4 in the live loop; the demo story; the inspector's new argument |
| `tests/unit/test_contracts.py`, `tests/contract/test_ports.py` | The new `Chunk` fields and `render_blocks()`; both options satisfy the `Chunker` port |
| `ui/tests/chunk.test.mjs` | Stale detail, size ranges, the size bar |

## 4. How it works

**Sections first.** `sections()` walks T3's blocks once. A heading opens a new section whose trail is the headings above it plus its own text; every block after it, until the next heading, belongs to that section. A section with nothing but its heading is left out:
```python
for block in blocks:
    if block.kind is BlockKind.HEADING:
        if _has_text(group) and trail is not None:
            yield trail, group
        trail, group = (*block.section, block.text), [block]
    elif block.section != trail:  # text before any heading, or a parser without headings
        ...
    else:
        group.append(block)
```
For `HR-PL-01` this gives four sections, each starting with its heading. The page title, `Annual leave policy`, has no text of its own (the next line is already `## Who this applies to`), so it makes no chunk. It isn't lost: it is the first entry of every chunk's `section`.

**Then fit each section to the limit.** `_fit()` counts the rendered tokens in each block, including list markers. If a complete list fits in a fresh chunk and the current chunk already holds body text, the chunker moves the list together when necessary. For example, with a ten-token limit, a six-token introduction followed by two four-token items yields chunks of six and eight tokens. Longer lists split between items. A section heading stays with its first body block, even when the pair exceeds the limit.

On our pages no section comes near 400 tokens (the largest is 59), so every section is one chunk: **48 chunks for 12 pages**. The splitting is proven by the tests with made-up long sections, and you can see it with `--max-tokens 30` (§7).

**The five rules, and why.**
1. *A chunk never crosses a section*, so each chunk is about one thing ("Booking leave", not "Booking leave and the start of Carrying leave over").
2. *The section’s first chunk includes its heading.* The fixed baseline also includes headings that serve only as parent titles. The full trail goes in `section`, as metadata, not in the text.
3. *A long section splits between blocks, never inside one.* Consecutive list items stay together whenever they fit. A continuation chunk keeps the trail but does not repeat the heading line: each body block appears exactly once.
4. *A table, or any single block, is never split.* One larger than the limit becomes a chunk of its own, over the limit, and the step counts it. A salary row cut from its header row no longer says which number is the maximum.
5. *Small sections are not merged.* `HR-CMP-01`'s "Restricted to the compensation team" is a 16-token chunk of its own, so every chunk has exactly one section.

**Text: one renderer for everything.** A chunk's text is `render_blocks(part)`, the same function behind `ParsedDocument.text`: list items keep their markers (`1.`, `-`), a table is one line per row with cells joined by ` | `, and blocks are separated by a blank line. `HR-PL-01#2` is:
```
Booking leave

1. Request leave in the HR portal at least two weeks before the first day off.
2. Your manager approves or declines within three working days.
3. Requests of more than ten consecutive working days need approval one month in advance.
```

**Tokens, approximately.** Sizes are in tokens because models count in tokens (input limits, context windows, prices). Every model has its own tokenizer, and loading one means a download, so T4 counts **a word or a punctuation mark** (`\w+|[^\w\s]`): `32,000 EUR` is 4 tokens. Accented letters are letters, so Italian and Spanish count like English. A real tokenizer splits long or rare words into pieces and counts a little more; the limits are therefore approximate. When the embedding model is chosen (S10), its tokenizer can replace this counter as a new version of each chunker.

**The baseline.** `fixed` takes the document's clean text, finds every token's position (`token_spans`), and cuts a window of `max_tokens` tokens, then the next one starting `overlap_tokens` before the previous end:
```python
end = min(start + self._max_tokens, len(spans))
window = text[spans[start][0] : spans[end - 1][1]]  # cut out of the text: line breaks kept
...
start = end - self._overlap
```
Its chunks have no section, on purpose. At the default `max_tokens=400, overlap_tokens=50` every synthetic page (84–188 tokens) is **one chunk**: the whole page, every topic of the policy in one passage. Shrunk to 60 tokens, the demo shows what a blind cut does to `HR-CMP-01`. Window `#1` starts in the middle of a row (`| 47,000 | 54,000`). Italy's `E3 | 52,000 | 61,000 | 70,000` lands in that window with neither its heading nor its header row, just above the heading for Spain.

**IDs, versions and what T4 never sets.** `chunk_id` is `<document_key>#<position>` (`hr_docs:HR-PL-01#2`). It names a place in a document; `document_version` says which version's text it holds, and a new version replaces the document's whole set of chunks. `chunker_id` records which option, version and settings cut it (`structure_aware@1(max_tokens=400)`), like T3's `parser_id` and T7's model ID, so a change of chunker can be planned as a re-chunk. `ChunkStep.check()` holds every option to the port's rules: right document and version, numbered 0 to n-1, not empty, and **no sensitivity, vector or ACL**:
```python
if chunk.sensitivity is not None or chunk.vector is not None or chunk.acl is not None:
    raise ChunkError(
        f"{where} already has a sensitivity, vector or ACL: only T5, T7 and T8 set them, ..."
    )
```
That is the publication gate's first line: `Chunk.publishable` needs all three, so nothing that leaves T4 can be searched until T8 has resolved its permissions.

**T4 behind T3, until S12.** `chunk_after_t3()` is T3's `keep`: it chunks what T3 parsed and only then hands both results on:
```python
def then(result: ParseResult) -> None:
    chunks = t4.handle(result.parsed) if result.parsed is not None else None
    keep(Processed(result, chunks))  # only after T4: a failure keeps nothing
```
So if T4 raises, neither T3's nor T4's result is kept, T2 records nothing, and the event comes back as new (S4's rule). The demo and the inspector wire it as `parse_after_t2(t3, chunk_after_t3(t4, keep), forget)`, where `forget` drops a purged document's parsed text **and** its chunks.

**The inspector.** `ekb inspector` keeps T4's latest chunks per document in a `ChunkStore`, with `runs` (how many times T4 chunked it: an edit adds one, a same-text save or a permission change doesn't). `GET /api/t4/chunks[?source=]` lists the chunked documents with their sizes; `GET /api/t4/chunks/document?document_key=` returns one document's chunks, or 404 for a record, an unknown item or a deleted one. Like T3's endpoints, both hide output from a lifetime a delete has closed, at once, even before T2's purge runs. The **T4 Chunk** tab shows the list and, for a clicked document, one card per chunk: its size as a bar against the limit (red when over), its section, its text, and a "not publishable yet" badge.

## 5. Options
Built now:
- `t4_chunk: structure_aware` (`max_tokens`, default 400): the starting choice of card 4.2, already named by `config/default.yaml`, so `ekb options` shows it as `[ready]`.
- `t4_chunk: fixed` (`max_tokens` 400, `overlap_tokens` 50): the baseline, already named by `config/profiles/ci.yaml`.

The demo and the inspector choose `structure_aware` by hand (`ekb.app.chunk`), as S4 and S5 did; choosing from config comes with the pipeline (S12). The config's `context_header` setting is not a chunker setting: in S8 the header becomes a port of its own in `t4_chunk` (none / a model, card 4.3), wired next to the chunker.

Later, each one more file in `options/` behind the same port:
- **semantic** splitting (card 4.2's third option): cut where the meaning shifts, which needs embeddings (after S10);
- **sentence splitting** for a single paragraph over the limit, and **merging** small sections, as settings measured by card 4.2;
- **an adaptive size**: a limit that follows the text instead of one number for every document. Your idea, for example a larger limit where words are longer, so chunks hold a similar amount of content. It needs the evaluation set (S18) to show it beats a fixed limit;
- **the embedding model's tokenizer** instead of the approximate counter (S10).

## 6. What the tests prove
- **Sections are respected:** one chunk per section, heading first, trail = the headings above plus its own; nested sections get three-level trails; text before any heading forms a chunk with no section; blocks that change section without a heading never share a chunk; small sections are not merged.
- **Nothing is lost or duplicated:** on all 12 pages every non-heading block appears in exactly one chunk; each heading either starts its section's chunk or (with no text of its own) is in every trail below it; each chunk's first line is the last entry of its trail; 48 chunks in total; Italy's salary table is one chunk, header row to `E3`.
- **Long sections split correctly:** between blocks, never inside; the heading is not repeated; a list stays together when it fits; a table bigger than the limit stays whole, over the limit; a document with no headings is split by size only.
- **The baseline:** windows hold exactly `max_tokens` and share exactly `overlap_tokens`; the end is covered and the last window isn't empty; windows are cut out of the clean text with line breaks; no section; each synthetic page is one chunk at 400; settings that would never move forward are refused.
- **The step's checks:** a chunk from another document or version, misnumbered, empty, or carrying an ACL, sensitivity or vector is refused (`ChunkError`). Over-limit chunks are counted.
- **Publication gate:** no chunk leaving T4 is `publishable`; the API shows `publishable: false` for every chunk; the demo ends with "Publishable chunks: 0 of 41".
- **Behind T2 and T3:** T4 runs once for new content and not for a repeat or a permission change; a record is never chunked; if T4 fails, nothing is kept and T2 records nothing, so a redelivery is never skipped.
- **Deletion gate, inspector side:** a purge drops the chunks; a published delete hides them at once even while T2 is stuck on an earlier event; a re-created page appears only after its new lifetime is chunked. In the live loop, editing `HR-PL-04` re-chunks it (`runs` 2), saving `HR-PL-01` unchanged or narrowing `HR-PL-05`'s readers keeps `runs` at 1, deleting `HR-PL-99` leaves 11 documents.
- **The port and the demo:** both options satisfy `Chunker`; the UI contract is current; the demo runs twice in a row, with `--chunker fixed --doc HR-CMP-01` and with `--max-tokens 30`, and rejects an unknown chunker, a fixed window not larger than its overlap, and an unknown page.

## 7. Run it yourself
```powershell
uv run ekb demo t4
uv run ekb demo t4 --chunker fixed --doc HR-CMP-01
uv run ekb demo t4 --max-tokens 30
```
You should see:
1. The initial load: 12 pages, one line each (`HR-PL-01   v1.1  13 blocks -> 4 chunks, 38-59 tokens`), then `48 chunks from 12 pages; 0 over the 400-token limit.`
2. `HR-PL-01` chunk by chunk: four chunks, each with its ID, size and trail (`hr_docs:HR-PL-01#2  49 tokens  § Annual leave policy > Booking leave`) and its text, then why the page title has no chunk of its own.
3. The baseline: `fixed@1(max_tokens=400,overlap_tokens=50): 12 chunks for 12 pages`, then `HR-CMP-01` in 60-token windows. A window starts in the middle of a row, and Italy's `E3` row sits without its heading or header row. Next to that, the structure-aware chunk keeps the Italy table whole under its heading.
4. Why headers matter: `hr_docs:HR-PL-01#3` ("You may carry up to 5 unused days…") never says "Annual leave policy"; only its trail does, and its context header is `''` until S8.
5. Four changes: `HR-PL-04` edited → `T2 process -> T3 3 blocks… -> T4 1 chunk, 7 tokens`; `HR-PL-01` saved unchanged → `T2 skip -> T3, T4 not run`; `HR-PL-05`'s readers narrowed → `T2 acl_only -> T3, T4 not run`; `HR-PL-99` deleted → `T2 purge`, its chunks forgotten, 41 kept from 11 pages.
6. `T3 and T4 ran 13 times` and `Publishable chunks: 0 of 41.`

With `--chunker fixed`, every page is one chunk and scene 4 says there is no section to speak of. With `--max-tokens 30`, sections split between blocks (67 chunks) and 13 chunks are over the limit: whole blocks, including their heading when they open a section.

In the inspector (`uv run ekb inspector`, then `npm run dev` in `ui/`), open **T4 Chunk**. The counters show 12 documents, 48 chunks and the chunker. Click `hr_docs:HR-CMP-01`: four cards, the two tables each whole in their own chunk. Edit a page in the docs tool's `/docs` page: its version and `runs` change and its chunks are new. Change only its readers: nothing changes here (T2's fast lane). Delete one: it disappears from the list.

## 8. Decisions and limits
- **Token counts are approximate:** words and punctuation marks, not a model's tokenizer. Good enough to keep chunks a sensible size; the real tokenizer can replace it when the embedding model is chosen (S10).
- **Our pages are short.** Every synthetic page is under 400 tokens, so `fixed` at its default never cuts and `structure_aware` never needs to split a section. The demo shrinks the window to show the effect. Card 4.2's experiment (S18) needs longer documents to measure a real difference.
- **A single block is never split.** A paragraph or table over the limit stays whole and is counted as over. Splitting a paragraph at sentences is a later setting; splitting a table would need its header row repeated in every part.
- **The heading is not repeated in continuation chunks.** Each body block appears once; a continuation chunk relies on its `section` (and, from S8, its context header) to say where it is from.
- **Chunk IDs are positions.** An edit that adds a section renumbers the chunks after it. That is fine because a new version replaces the whole set. A per-chunk content hash, which lets T7 reuse vectors for unchanged chunks (D16 #8), comes with embeddings (S10).
- **No context header yet.** `context_header` is `""` on every chunk. S8 writes it with a model through the gateway (S7), one call per document (D16 #4), and card 4.3 measures what it adds.
- **T4 runs inside T2's downstream**, synchronously, in the demo and the inspector. S12 puts it on the bus.
- **Chunks live in memory**, latest version only, in the inspector and the demo. Where chunks are kept between steps is decided with the pipeline (S12); the search store that publishes them comes with T8 (S11).

## 9. Check your understanding
1. lucia removes `hr-team` from `HR-PL-05`'s readers. Does T4 run? What happens to the page's chunks, and which step will change who can find them?
2. With `fixed` at 60 tokens, what goes wrong with `HR-CMP-01`'s salary tables, and which two rules of `structure_aware` prevent it?
3. `hr_docs:HR-PL-01#3` doesn't contain the words "Annual leave policy". Where is that information kept, why isn't it added to the chunk's text now, and what will add it?

<details>
<summary>Answers</summary>

1. No. T2 sees the same content hash with different readers and decides `acl_only`, the fast lane, so neither T3 nor T4 runs: the text didn't change, so the chunks would be identical (in the inspector its `runs` stays at 1). The chunks stay as they are. Their permissions are not set by T4 at all: T8 (S11) resolves the new ACL and applies it to the existing chunks, which is exactly what the fast lane is for.
2. A window starts in the middle of a row, and Italy's `E3 | 52,000 | 61,000 | 70,000` ends up in a window with neither its heading nor its header row, just above the heading for Spain. A search would return numbers that no longer say which country they are for or which column is the maximum. `structure_aware` prevents it with rule 1 (a chunk never crosses a section, so Italy and Spain are separate chunks, each with its heading) and rule 4 (a table is never split, so the header row stays with every row).
3. In the chunk's `section`, `("Annual leave policy", "Carrying leave over")`, as metadata. The page title has no text of its own, so it starts no chunk. The fixed baseline includes the title in the page text. Structure-aware chunks carry that parent title in their trails. Card 4.3 measures the effect of adding a context header. In S8 a model writes the context header through the gateway (S7), one call per document, so the chunk is found by questions about annual leave.
</details>
