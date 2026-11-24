# S05 · T3 Parse: from a stored original to clean, structured text

> Architecture: `docs/architecture.md` (box 4, "T3 · Parse"; transformations table: "File → clean text with structure"). Plan: `docs/plan.md` §4.2, S5. Option card: `docs/option-evaluation.md` 4.1 (parsing: "tables and headings survive (they drive chunking)"). Related decisions: D7 (structured data is queried, not embedded), D10 (Docling by default, Bedrock Data Automation only for scans).

## 1. In one paragraph
T2 (S4) decides which change events carry new or changed content. T3 is the first step that looks at that content. It reads the original the event points to from the raw store, takes the document out of the source's envelope (the docs tool stores its whole page JSON, with the Markdown in a `body` field), and parses it into **blocks**: headings, paragraphs, list items, tables and code, in reading order. Every block also carries its **section**, the headings above it. That structure is the point of the step. T4 (S6) cuts chunks along sections, a chunk that knows it belongs to "Annual leave policy > Booking leave" is found for the right questions, and a table kept whole still says which number is the maximum. T3 parses documents only: HR records, database rows and identity users are structured records, which are queried, never parsed and embedded (D7).

## 2. Where it sits in the flow
```
T2 (S4) ── process ──▶ [ T3 ]
                          1. rawstore.get(event.raw_pointer)        the exact bytes this version had
                          2. reader(key, version, bytes)            the source's envelope -> SourceFile
                          3. parser.parse(file)                     the option: markdown, plain_text
                       ──▶ T4 (S6)

input:  ChangeEvent (an upsert T2 decided to process)
output: ParseResult(event, parsed: ParsedDocument | None, reason)
        ParsedDocument(document_key, version, title, language, media_type, parser_id, blocks)
```
`skip` and `acl_only` never reach T3; `purge` doesn't either, but whoever keeps T3's output must drop the item's parsed text (§4).

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/contracts/parsed.py` | The new contract T4 will read: `BlockKind`, `Block` (its validator refuses empty blocks, a heading level outside 1–6, a list item without depth, a table without rows), `ParsedDocument` and its `.text` (clean text) |
| `src/ekb/process/t3_parse/port.py` | `Parser` (`parser_id`, `parse(file)`), `SourceFile`, `ParseError`, `decode_text()` shared by the text parsers |
| `src/ekb/process/t3_parse/options/markdown.py` | `markdown` option: `markdown-it-py` (CommonMark + tables) and `_Walker`, which turns its tokens into blocks |
| `src/ekb/process/t3_parse/options/plain_text.py` | `plain_text` option: the structure-blind baseline of card 4.1 |
| `src/ekb/process/t3_parse/originals.py` | `OriginalReader` and `docs_page()`: the docs tool's page JSON → its Markdown body, title and language |
| `src/ekb/process/t3_parse/step.py` | `ParseStep.handle(event)`, `ParseResult`, `summary()` |
| `src/ekb/app/parse.py` | `DOCUMENT_SOURCES` (`hr_docs` → `docs_page`), `parser(name)`, `parse_step()`, `parse_after_t2()` (T2's downstream until S12) |
| `src/ekb/app/inspector.py` | The sync loop runs T3 after T2 and keeps the results in a `ParsedStore` |
| `src/ekb/app/demos/t3.py` | `ekb demo t3` |
| `src/ekb/operate/inspector/{log,models,app}.py` | `ParsedStore`, `ParsedEntry`; views; `GET /api/t3/parsed`, `GET /api/t3/parsed/document` |
| `ui/src/components/ParseScreen.tsx`, `parse.ts`, `App.tsx`, `styles.css`, `api/client.ts` | **T3 Parse** tab |
| `ui/openapi.json`, `ui/src/api/schema.d.ts` | Regenerated contract |
| `pyproject.toml`, `uv.lock` | `markdown-it-py` becomes a direct dependency (it was already locked at 4.3.0 through `rich`) |
| `tests/unit/process/t3_parse/test_markdown.py`, `test_plain_text.py`, `test_originals.py`, `test_step.py`, `conftest.py` | The parsers, the reader and the step, failure cases included; every synthetic page |
| `tests/unit/app/test_parse.py` | T3 behind T2: what is parsed, what is forgotten, what happens when T3 fails |
| `tests/unit/test_contracts.py`, `tests/contract/test_ports.py` | The block rules and `.text`; both options satisfy the `Parser` port |
| `tests/unit/operate/inspector/test_inspector.py`, `tests/unit/app/test_inspector_command.py`, `test_demos.py` | The endpoints and the purge rule; T3 in the inspector's loop; the demo story |
| `ui/tests/parse.test.mjs` | Section labels, trails, kind summaries |

## 4. How it works

**Two parts: open the envelope, parse the file.** What a source stores is not always the file. The docs tool's original is its API answer for one page: `id, title, language, department, acl, rev, acl_rev, content_hash, updated_at, modified_by, body`. A SharePoint connector would store the `.docx` itself. So an **original reader** (`originals.py`) knows one source's envelope and returns a `SourceFile` (key, version, title, language, media type, bytes), and a **parser** (`options/`) knows one file format. Docling, later, gets the same `SourceFile`. Which reader goes with which source is chosen in the wiring, not guessed from the bytes:
```python
DOCUMENT_SOURCES: dict[str, OriginalReader] = {"hr_docs": docs_page}
```
The step puts the parts together:
```python
reader = self._readers.get(event.source)
if reader is None:
    return ParseResult(event, None, f"not a document: {event.source} holds structured records, ...")
original = self._rawstore.get(event.raw_pointer)
parsed = self._parser.parse(reader(event.document_key, event.version, original))
```
T3 reads the **raw store, not the source**. The source may have changed again, or be down, and the stored original is exactly the version the event names. A test stores 1.1 and 2.1 and checks that the event for 1.1 gets 1.1's text.

**Blocks, each with its section.** `ParsedDocument.blocks` is a flat list in reading order:

| kind | `text` | extra fields |
|---|---|---|
| `heading` | the heading | `level` 1–6 |
| `paragraph` | the paragraph, spaces collapsed | — |
| `list_item` | the item's text | `level` = nesting depth from 1, `marker` = `-` or `3.` (`""` for a second paragraph of the same item) |
| `table` | one line per row, cells joined by ` \| ` | `rows`, header row first |
| `code` | as written | — |

and every block has `section`, the headings above it, outermost first. For `HR-PL-01` the step "Your manager approves or declines within three working days." is a `list_item` with `marker="2."` and `section=("Annual leave policy", "Booking leave")`. Flat with a trail, rather than a tree, because T4 can then cut anywhere and still name where a passage came from, and because one block per line is easy to read and test.

**The walker.** `markdown-it-py` turns Markdown into tokens (`heading_open`, `inline`, `heading_close`, `bullet_list_open`, `list_item_open`, `table_open`, `tr_open`, `td_open`, ...). `_Walker` reads them once, keeping three things: the headings above (for `section`), the lists it is inside (for `level` and `marker`) and the table rows so far (to emit a table as one block). A heading closes every section at its level or deeper:
```python
while self.headings and self.headings[-1][0] >= level:
    self.headings.pop()
self._add(BlockKind.HEADING, text, level=level)  # its section: the headings above it
self.headings.append((level, text))
```
A real parser instead of hand-written rules: nested lists, a `|` escaped inside a cell and `Title` over `=====` are easy to get wrong, and a parsing mistake here becomes a wrong chunk boundary in T4. Raw HTML is not interpreted (`html: False`): it stays as text, so nothing on a page is silently dropped.

**Clean text.** `inline_text()` keeps what a reader sees: text and code, an image's alt text, a line break as a space. Formatting marks and link targets go: `**25 working days**` becomes `25 working days`. `ParsedDocument.text` is the whole document as clean text, blocks separated by a blank line, list items kept together and indented by depth; the inspector's "Clean text" view shows it.

**The baseline.** `plain_text` splits the body at blank lines and stops there: Markdown symbols stay, no block knows its section, a list is one paragraph, a table a paragraph of pipes. `HR-PL-01` gives 13 blocks with `markdown` and 5 paragraphs with `plain_text`. It exists to be measured against (card 4.1's first experiment), and the `ci` profile names it.

**`parser_id`.** Every result says which parser and version produced it (`markdown-it-py@4.3.0`, `plain_text@1`), like T7's model ID. A parser upgrade then shows up as a different ID, so re-parsing can be planned instead of leaving two kinds of blocks side by side.

**Errors are errors.** A missing original raises `OriginalNotFoundError`; an original that isn't a page (bad JSON, no `body`, not UTF-8) or a format the parser doesn't read raises `ParseError`. T3 never returns "nothing" for a document it couldn't read.

**T3 behind T2, until S12.** The pipeline puts T3 on the bus in S12. Until then the demo and the inspector call it from T2's `downstream`, through `parse_after_t2`:
```python
if decision.action is DiffAction.PROCESS:
    keep(t3.handle(decision.event))
elif decision.action is DiffAction.PURGE:
    forget(decision.event.document_key)
```
`skip` and `acl_only` do nothing: the text didn't change, so the blocks wouldn't either. `purge` makes whoever keeps T3's output forget the item: a deleted page's parsed text goes with its originals. Because T3 runs inside T2's downstream, S4's rule holds: if T3 raises, T2 records nothing and the event comes back, decided as new again, never skipped as a repeat. In the inspector, T3 runs *before* the decision is logged, so a failure leaves no half-recorded decision either.

**The inspector.** `ekb inspector` keeps T3's latest result per item in a `ParsedStore`: the document, when it was parsed, and `runs` (how many times T3 parsed it; an edit adds one, a same-text save doesn't). It also counts, per source, the items T3 found to be records. `GET /api/t3/parsed[?source=]` lists the parsed documents (with block counts per kind) and those record counts; `GET /api/t3/parsed/document?document_key=` returns one document's blocks and clean text, or 404 when it isn't there (a record, not processed yet, or deleted). The **T3 Parse** tab shows the list and, for a clicked document, its blocks (kind on the left, the section shown once above its first block, tables as tables) or its clean text.

## 5. Options
Built now: `t3_parse: markdown` (the docs tool's pages are Markdown) and `t3_parse: plain_text` (the baseline; already named by `config/profiles/ci.yaml`). `config/default.yaml` still names `docling`, the starting choice of card 4.1, because a default changes only with an experiment result; `ekb options` therefore shows `docling [not built yet]` with `available: markdown, plain_text`. The demo and the inspector choose `markdown` by hand (`ekb.app.parse`), as S4 did for T2; choosing from config comes with the pipeline (S12). Later: `docling` as an optional extra (PDF, Word, slides, layout and tables), and Bedrock Data Automation for scanned documents only (D10). Each is one more file in `options/` reading the same `SourceFile`.

## 6. What the tests prove
- **Structure survives:** each block has the headings above it, and a heading closes deeper sections; lists keep depth, numbers and `.`/`)`; a second paragraph in an item is a continuation; a table is one block with its cells (an escaped `|` stays in its cell, an empty cell is kept); code keeps its text; Setext headings and Windows line endings work.
- **Every synthetic page:** all 12 parse; the first block is the page title as an `h1`; everything after it has a section; no `**` is left. `HR-CMP-01` gives two tables of 4 × 4, E2's row intact.
- **Clean text:** formatting and link targets are removed and the words stay; raw HTML stays as text; `.text` keeps list items together and indents nested ones.
- **Failures are loud:** invalid UTF-8, a format the parser doesn't read, an original that is not a page (not JSON, no `body`, a list) all raise, naming the item and version; a missing original raises; a delete or ACL change sent to T3 is refused.
- **Right version, right sources:** T3 parses the version the event names, not the latest; a record is "not a document" and its original isn't read.
- **Behind T2:** T3 runs once for new content and not for a repeat or a permissions change; a purge makes the parsed text be forgotten; if T3 fails, T2 records nothing, so a redelivery is never skipped.
- **Deletion gate, inspector side:** publishing a delete closes the document's lifetime. Both parsed endpoints hide output from that lifetime immediately, even if an earlier parse failure delays T2's purge. A re-created page appears after its new lifetime is parsed. In the live loop, deleting `HR-PL-99` leaves 11 documents, editing `HR-PL-04` re-parses it (`runs` 2), and saving `HR-PL-01` unchanged keeps `runs` at 1.
- **Determinism and the port:** the same file gives the same result; both options satisfy `Parser`; the UI contract is current; the demo runs twice in a row and with `--doc HR-PL-09 --parser plain_text`, and rejects an unknown parser or page.

## 7. Run it yourself
```powershell
uv run ekb demo t3
uv run ekb demo t3 --doc HR-CMP-01
uv run ekb demo t3 --parser plain_text
```
You should see:
1. The initial load: 12 pages parsed with `markdown-it-py@4.3.0`, one line each (`HR-PL-01 v1.1 13 blocks: 5 heading, 2 paragraph, 6 list_item`).
2. `HR-PL-01` end to end: 1,236 bytes of JSON and its fields, the Markdown body, then the 13 blocks (`list_item 1.  Request leave in the HR portal…`), the section of one item, and `'**25 working days**' in the Markdown is '25 working days' in the text`.
3. `HR-CMP-01`'s two tables, each 4 rows × 4 columns under its own section, the first printed as a grid.
4. The same original parsed by both options: `markdown 13 blocks` against `plain_text 5 blocks: 5 paragraph`, with plain text's first paragraphs (`## Who this applies to` glued to its text).
5. Four changes at once: `HR-PL-04` edited → `T2 process -> T3 parsed … 3 blocks`; `HR-PL-01` saved unchanged → `T2 skip -> T3 not run`; `HR-PL-05`'s readers narrowed → `T2 acl_only -> T3 not run`; `HR-PL-99` deleted → `T2 purge`, its parsed text forgotten, 11 kept.
6. `T2 decided 16 events: process 13, acl_only 1, skip 1, purge 1. T3 ran 13 times`.

In the inspector (`uv run ekb inspector`, then `npm run dev` in `ui/`), open **T3 Parse**. The counters show 12 documents parsed and the records not parsed (`hr_api` 17, `hr_db` 16, `identity` 12). Click `hr_docs:HR-CMP-01` for its tables, switch to **Clean text**, then edit a page in the docs tool's `/docs` page: its version and `runs` change. Save one without changing the text: nothing changes here (T2 skipped it). Delete one: it disappears from the list.

## 8. Decisions and limits
- **Documents only.** Only `hr_docs` has an original reader. HR records, database rows and identity users reach T3 and come back as "not a document"; the entity store (S9) and the metrics store (S14b) take them (D7).
- **The format comes from the source, not the event.** Events carry no media type, so the wiring maps each document source to its reader. When a second document source arrives, a `media_type` on the event or on the stored original is the cleaner answer (a contract change).
- **Formatting is dropped.** Bold sometimes marks the key number (`**25 working days**`); the words stay, the emphasis doesn't. Link targets are dropped too. Task-list checkboxes (`- [ ] …` in `HR-PL-09`) stay as `[ ]` text: CommonMark doesn't know them.
- **No dead-letter queue.** A broken original fails its event every time it comes back, and in the inspector it is retried every round. Setting such events aside after a few tries is the pipeline's job (S12).
- **T3 runs inside T2's downstream**, synchronously, in the demo and the inspector. S12 puts it on the bus.
- **Parsed output lives in memory**, latest version only, in the inspector and the demo. Where parsed documents are kept between steps is decided with the pipeline (S12).

## 9. Check your understanding
1. dana saves `HR-PL-01` again without changing a word. Does T3 run? Which step decides, and why is it safe not to parse again?
2. Why does T3 read the original from the raw store instead of fetching the page from the docs tool when the event arrives?
3. In `HR-PL-01`, what is the section of the step "Your manager approves or declines within three working days.", and why does T4 care? What does `plain_text` give for the same text?

<details>
<summary>Answers</summary>

1. No. T2 compares the event's content hash and readers with the registry, finds both unchanged, and decides `skip`, so T3 is never called. It is safe because T3 is deterministic: the same original with the same parser (same `parser_id`) gives the same blocks, so parsing again would only cost time.
2. Because the event names a version, and the stored original is exactly that version. By the time T3 runs the page may have been edited again, deleted, or the docs tool may be down; fetching it would parse the wrong version or fail. Keeping originals also lets a new parser re-parse everything later from the raw store without asking the sources again.
3. `("Annual leave policy", "Booking leave")`, with `marker="2."`. T4 cuts chunks along sections and can name the section in a chunk's context header, so the step is found for "how do I book leave?" even though it never says "leave". `plain_text` puts it inside one paragraph that starts `## Booking leave`, with the other two steps, and no block knows its section.
</details>
