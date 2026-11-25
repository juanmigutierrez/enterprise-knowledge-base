# S08 · T4 context header + T5 Classify: what metadata each chunk carries, and why

> Architecture: `docs/architecture.md` (box 4, "T4 · Chunk + context header" and "T5 · Classify"; transformations table: "Chunk → department, sensitivity, PII flags"). Plan: `docs/plan.md` §4.2, S8. Option cards: `docs/option-evaluation.md` 4.3 (context header: none vs a model) and 4.9 (classification: rules, model, model + rules; "a missed confidential label is a security incident; an extra label is just friction"). Decisions: D15 (the `context_header` and `classify` tasks), D16 #1 (cache), #4 (one call per document), #7 (capped output).

## 1. In one paragraph
S6 cut documents into chunks; this step adds what each chunk carries besides its text. **T4's context header** is one or two sentences that put a chunk back in its document. `HR-PL-01#3`, "You may carry up to 5 unused days into the next year…", never says "annual leave", so a search has little to match; its header can. A model writes the headers through the model gateway (S7), **one call per document**, cached by content and forgotten when the document is deleted. **T5** then labels every chunk with a **department**, a **sensitivity** (public, internal, confidential, restricted) and the **personal data** (PII) it holds. Those are what the access layer will filter on (Q2, S13), and `sensitivity` is one of the three fields the publication gate needs. The rules look for explicit markers and personal-data patterns, never for topic words. The model option only makes labels stricter, never laxer.

## 2. Where it sits in the flow
```
T2 (S4) ── process ──▶ T3 (S5) ──▶ [ T4 (S6 + S8) ]                     ──▶ [ T5 (S8) ]                           ──▶ T6 … T8
                                      1. chunker.chunk(parsed)                 1. classifier.classify(parsed, chunks)
                                      2. header.write(parsed, chunks)          2. labelled(): one set of labels per chunk
                                         └ gateway: context_header,              └ model_plus_rules: gateway, classify,
                                           one call per document                    one call per document
        ── purge ──▶ forget: parsed text, chunks, and gateway.forget(document_key)

input:  ParsedDocument(document_key, version, title, language, department, blocks)   ← department: new in S8
output: Chunk(…S6 fields…, context_header, header_id,                                 ← T4 header
              department, sensitivity, pii, classifier_id,                            ← T5
              entity_ids=(), acl=None, embedding_model=None, vector=None)
```
Both run only where T2 decided `process`. `skip` (same text) and `acl_only` (only the readers changed) never reach them: the header and the labels come from the text, and the text didn't change. A `purge` now drops the document's **cached model replies** too.

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/contracts/knowledge.py`, `__init__.py` | `Sensitivity.rank` and `strictest()`; `PiiKind` with `high_risk`; `Chunk` gains `header_id`, `pii` and `classifier_id`, with docstrings for every label |
| `src/ekb/contracts/parsed.py`; `process/t3_parse/{port,originals}.py`, `options/{markdown,plain_text}.py` | `department` passed from the docs tool's page through `SourceFile` to `ParsedDocument` |
| `src/ekb/platform/gateway/port.py` | `Completer`: what a step needs from the gateway (`complete`). `ModelGateway` implements it |
| `src/ekb/platform/gateway/options/fake.py` | The fake fills a JSON schema in (first enum value, empty list, nested objects) |
| `src/ekb/process/t4_chunk/header.py` | `HeaderWriter` port, `Headers`, `HeaderOutcome` (`none`, `written`, `cached`, `failed`), `clean()` |
| `src/ekb/process/t4_chunk/options/header_none.py`, `header_model.py` | `t4_header: none` and `t4_header: model`: prompt, schema, reading the reply, failure handling |
| `src/ekb/process/t4_chunk/step.py` | `ChunkStep(chunker, header)`, `with_headers()`: one header per chunk, nothing else changed |
| `src/ekb/process/t5_classify/port.py` | `Classifier`, `Labels`, `Classification`, `DEPARTMENTS` |
| `src/ekb/process/t5_classify/pii.py` | Personal-data patterns with check digits (IBAN, card, DNI / NIE) and masking |
| `src/ekb/process/t5_classify/rules.py` | Markers, department keywords, `labels()`: the rules shared by both options |
| `src/ekb/process/t5_classify/options/rules.py`, `model_plus_rules.py` | `t5_classify: rules` and `t5_classify: model_plus_rules` |
| `src/ekb/process/t5_classify/step.py` | `ClassifyStep`, `ClassifyResult`, `check_input()`, `labelled()`, `summary()` |
| `src/ekb/app/chunk.py` | `header_writer()`, `chunk_step(header=…)`, `Processed.labels` and `final`, `chunk_after_t3(t4, keep, t5)` |
| `src/ekb/app/classify.py` | `classifier()`, `classify_step()`, `forgetter()`: a purge drops the stores, then the gateway's replies |
| `src/ekb/app/inspector.py`, `operate/inspector/{log,models,app}.py` | The live loop runs the header and T5, `--header` / `--classifier`; `ChunkEntry` notes and reasons; `ChunkView` and `ChunkSummaryView` carry the labels |
| `src/ekb/app/demos/t5.py`, `demos/__init__.py`, `demos/t4.py` | `ekb demo t5`; the t4 demo points to it |
| `config/default.yaml`, `profiles/ci.yaml`, `profiles/bedrock.yaml` | `t4_header` is its own entry (`model`; `none` in `ci`); `context_header` left the chunker's params |
| `ui/src/components/ChunkScreen.tsx`, `chunk.ts`, `styles.css`; `ui/openapi.json`, `src/api/schema.d.ts` | Headers, sensitivity badges, department, PII and "why" on the **T4 Chunk** tab; regenerated contract |
| `tests/unit/process/t4_chunk/test_header.py` | Both header options and the step |
| `tests/unit/process/t5_classify/*` | The patterns, the rules, `model_plus_rules`, the step |
| `tests/unit/app/test_classify.py`, `test_inspector_command.py`, `test_demos.py` | The wiring behind T2, the live loop, the demo |
| `tests/unit/platform/gateway/helpers.py` (`Scripted`), `test_clients.py`, `tests/contract/test_ports.py`, `tests/unit/test_contracts.py`, `t3_parse/test_originals.py`, `operate/inspector/test_inspector.py`, `ui/tests/chunk.test.mjs` | A scripted `Completer` for any reply; the fake's JSON; the two new ports; contracts; the department; the API views; the UI helpers |

## 4. How it works

### The header: a port that only returns strings
`HeaderWriter.write(document, chunks)` returns a `Headers`: **one string per chunk**, the `header_id` of what wrote them, an outcome and a note. The step puts the strings on the chunks:
```python
return tuple(
    chunk.model_copy(update={"context_header": clean(text), "header_id": headers.header_id})
    for chunk, text in zip(chunks, headers.texts, strict=True)
)
```
So a header writer, whatever model it calls, cannot change a chunk's text, its position or a field a later step owns. A writer that returns the wrong number of headers is refused (`ChunkError`). Without a writer (the S6 wiring, still used by `ekb demo t4`), chunks keep an empty header and an empty `header_id`.

### The `model` option: one call per document
The model sees the document once, then the numbered chunks:
```
<document title="Annual leave policy">
…the whole document…
</document>

Write a header for each of these 4 passages of the document:

<chunk_0>
Who this applies to
…
</chunk_0>
```
The system prompt asks for one or two sentences per passage, at most 50 words, saying which document it is from and what it is about "in the words an employee would search with", naming the country or office when the document gives one, using only facts in the document, **in the document's language** (Italian pages get Italian headers). The JSON schema has one required string per chunk, `chunk_0` to `chunk_n-1`, so the schema itself says how many headers are expected:
```python
"properties": {key(position): {"type": "string", …} for position in range(count)},
"required": [key(position) for position in range(count)],
```
There is no `additionalProperties`, because not every provider's structured output accepts it; extra keys are ignored. The output limit is `256 + 96 × chunks`, capped at 4,096 tokens (D16 #7). The extra 256 leaves room for models that think before answering.

**Through the gateway.** The call names the task `context_header`, never a model, with `prompt_version="header@1"` and `document_key`. So everything S7 built applies:
- the chain Gemini Flash-Lite → gpt-oss-20b → Mistral Small → OpenRouter free;
- quotas and failover;
- the **cache**: the key holds the document's text, so an unchanged page costs nothing the next time and an edited one gets new headers;
- a **tag** with the document key on the cached reply.

`header_id` records the model that answered, e.g. `model@1(header@1, groq:openai/gpt-oss-20b)`, because failover can change it from day to day.

**Never blocks the document.** If the gateway raises (no key, every quota used up, an invalid request) or the reply is not a JSON object, every header is left empty, `header_id` says `no header`, and the note says why. A missing key for one chunk leaves that header empty and the note counts it. The header is an enrichment, not a gate: a chunk without one is still a correct chunk, but an event retried forever would spend quota and hold up freshness.

### T5: labels from the content, never from the ACL
`Classifier.classify(document, chunks)` returns one `Labels(sensitivity, department, pii, why)` per chunk. It reads the whole document because some signals belong to it: the department the source files it under, or a restriction written once at the top. The step puts the labels on the chunks and checks them (§6). **Who may read a document plays no part.** That is T8's job, and a permission change takes T2's fast lane, which never runs T5: a label that depended on the ACL would go stale there.

**The rules** (`rules.py`, shared by both options):

| Signal | Effect | Scope |
|---|---|---|
| "(restricted)" in the title, "Restricted to …", "Do not forward", "riservato al …", "non inoltrare", "restringido al …", "no reenviar" … | `restricted` | every chunk of the document |
| "(confidential)" in the title, a line starting "Confidential:" (and IT / ES) | `confidential` | every chunk of the document |
| an IBAN, a payment card, a codice fiscale, a DNI / NIE in the chunk | at least `confidential` | that chunk only |
| an email address, a phone number | flagged in `pii`, level unchanged | that chunk |
| nothing of the above | `internal` | |
| — | `public` is never guessed | |

A document marker is a **floor** for every chunk because restrictions are written once. `HR-CMP-01` says "Restricted to the compensation team. Do not forward." at the top, and its "Review cycle" chunk never says it again. It is still restricted, because a missed restriction is a leak and an extra one only friction (card 4.9). Personal data is per chunk: an IBAN in one example section doesn't make a whole policy confidential.

**Markers, not topic words.** The code of conduct says shopper data "are confidential"; the remote work policy pays an allowance "with the salary". A word list would label both confidential, and that friction grows with every document. The rules ignore them (§7, scene 4).

T5 scans each chunk's context header together with its text. A header can repeat personal data from elsewhere in the document, so both classifier options include it when labelling the passage.

**Personal data** (`pii.py`): regular expressions, and where an identifier has check digits, the check digits too. An IBAN must pass ISO 13616's mod 97, a card number the Luhn check, a DNI or NIE its control letter. That turns "a long number" into "almost certainly an identifier": `32,000 EUR`, an order number or a policy code never fires. Each match is blanked out before the next detector runs, so an IBAN's digits are not also reported as a card or a phone. Names are not found by patterns; only a model (or Presidio, later) finds them.

**Department.** The docs tool files every page under a department (`people`, `finance`), and that label now travels through T3 (`ParsedDocument.department`) and **wins**. Only when a source has none are keywords counted, in English, Italian and Spanish (title three times, text once). A tie means no department.

**`model_plus_rules`.** The rules run first and always answer. Then one call per document to the gateway's `classify` task (`classify@2`) asks for a department and, per chunk, a sensitivity and the PII kinds, with enums in the schema. The two are combined chunk by chunk:
```python
if level.rank > sensitivity.rank:
    sensitivity, why = level, f"{level}: the model said so (rules: {labels.sensitivity})"
```
- **Sensitivity:** the stricter of the two. The model can raise a chunk ("Alice Rossi moves to Madrid" with a name), never lower it below the rules.
- **Personal data:** everything either found. A high-risk kind only the model found still raises the chunk to confidential.
- **Department:** the rules'; the model's only when the rules have none.

If the model is unavailable or its reply doesn't parse, the rules' labels stand and the note says `rules only: …`. A model outage makes labels less sharp, never missing.

### Wiring, and what a purge forgets
`chunk_after_t3(t4, keep, t5)` runs T4 (with its header) and then T5, and only then calls `keep`. If either raises, nothing is kept, T2 records nothing and the event comes back (S4's rule). `Processed.final` is the chunks as the last step left them.

`forgetter(gateway, *drops)` is T3's `forget` for a purge: it drops the document from every store, **then** calls `gateway.forget(document_key)`. That drops every cached reply tagged with the document and no other: a header paraphrases the text of the document, and a deleted document must leave nothing readable behind. This is the wiring S7 left for S8. The gateway goes last: if a store fails, the purge is retried, and forgetting twice is harmless.

### The inspector and the UI
`ekb inspector` builds the gateway from the settings and runs the header (`--header model`, the default) and T5 (`--classifier rules`). Its Model gateway tab reads the **same** cost log instance, so the header calls appear there. Without keys, it prints that no model wrote the headers and how to fix it. `GET /api/t4/chunks` gains each document's chunks per sensitivity and the two notes; `GET /api/t4/chunks/document` gains, per chunk, `header_id`, `department`, `sensitivity`, `pii`, `classifier_id` and `why`. The **T4 Chunk** tab shows:
- the header as an italic line (hover: who wrote it);
- a sensitivity badge, red for restricted (hover: why);
- the department and PII as tags;
- a sensitivity column in the list.

## 5. Options
Built now, chosen by hand in `ekb.app` (choosing from config comes with the pipeline, S12):
- `t4_header: none`: the baseline of card 4.3 and the `ci` profile's choice.
- `t4_header: model`: the configured default (`t4_header: { option: model, card: "4.3" }`); the demo and the inspector use it.
- `t5_classify: rules`: the `ci` choice, and what the demo and the inspector use by default.
- `t5_classify: model_plus_rules`: the configured default (card 4.9's starting choice); `--classifier model_plus_rules` in the demo and the inspector.

`context_header` left `t4_chunk`'s params and became its own `t4_header` entry, so `ekb options` lists it as `[ready]`. The choices didn't change.

Later, each one more file behind the same ports:
- **`model` alone** for T5 (card 4.9's third option), measured against a labelled set in S18;
- **Presidio** as a rules option: names, addresses and more patterns, with a spaCy model;
- **a stronger header model** (card 4.3's Sonnet, or a different chain): configuration only, `gateway.tasks.context_header`;
- **prompt caching** of the document prefix (D16 #2), and **one combined call** for T4, T5 and T6 (D16 #4) if an experiment shows it is worth coupling the steps.

## 6. What the tests prove
- **Header, `none`:** an empty header per chunk, `none@1`.
- **Header, `model`:**
  - one gateway request per document, task `context_header`, prompt version `header@1`, tagged with the document key;
  - the same document again is a cache hit and reaches no model; an edited one makes a new request;
  - `gateway.forget` drops the reply;
  - the prompt holds the title, the document and each tagged passage, and the system prompt names the document's language;
  - the schema requires one key per chunk and has no `additionalProperties`; the output limit is capped.
- **Reading the reply:** extra keys are ignored. A missing or non-string header leaves that one empty and is counted. Text that is not a JSON object, or no model available, leaves every header empty, outcome `failed`, with the reason. Long headers are cut to the word limit on one line. No chunks, no call.
- **The T4 step:** headers change nothing but `context_header` and `header_id`; the wrong number of headers is a `ChunkError`; a header opens no gate; without a writer, S6's behaviour is unchanged.
- **Patterns:** each kind is found in realistic text, including IT / ES formats. Salary tables, amounts, dates, policy codes, a 13-digit order number, an IBAN one digit off and a DNI with the wrong letter are not. An IBAN is not also a card.
- **Rules:**
  - `HR-CMP-01` is restricted down to its last chunk; the eleven other synthetic pages are internal with no PII.
  - The markers work in three languages; topic words ("confidential" in a sentence, "salary", "restricted after hours") don't count.
  - An IBAN raises only its chunk, an email and a phone only flag it, a document marker beats chunk PII, `public` is never guessed.
  - The source's department wins; keywords name it otherwise; no keyword or a tie means none.
- **`model_plus_rules`:**
  - the model raises, never lowers, and a restricted page stays restricted whatever the model says;
  - high-risk PII only the model found raises the chunk; PII is the union;
  - department: the source's first, the model's only when there is none, an unknown one ignored;
  - no model or a bad reply means rules only, said in the note; a garbled chunk keeps its rules labels;
  - one call per document, cached and forgotten.
- **The T5 step:** labels change nothing else and open no gate. PII is kept once each in a fixed order, the department lowercased. The wrong number of labels, a missing sensitivity, chunks from another document, or chunks that already have an ACL or a vector are refused (`ClassifyError`).
- **Behind T2:** the header and T5 run once for new content, never for a repeat or a permission change. If T5 fails, nothing is kept and T2 records nothing (the event comes back). **Deletion gate: a purge drops the stores, then both cached replies about the document** (cache size 2 → 0).
- **The live loop:** headers and labels on all 48 chunks, `HR-CMP-01` restricted. Deleting `HR-PL-99` drops its header reply (12 → 11). The loop builds without a gateway when no model option is chosen, refuses `model` without one, and the command refuses unknown options.
- **API and UI:** the new fields and notes are served, the contract is regenerated, the UI helpers are tested.
- **The demo:** the full story with fake models. A second run on the same gateway finds the headers cached (1 request, 11 hits). It also runs with `--header none --classifier model_plus_rules` and without any key (empty headers, said why). It refuses unknown options and reports an unreachable mock.

## 7. Run it yourself
```powershell
uv run --env-file .env ekb demo t5                     # headers from your free tiers
uv run ekb demo t5 --fake                              # offline: placeholder headers
uv run ekb demo t5 --fake --classifier model_plus_rules
uv run ekb demo t5 --header none
```
You should see:
1. **Initial load:** 12 pages, one line each (`HR-CMP-01  4 chunks  headers written  4 restricted`), then `48 chunks from 12 pages; 48 have a header.` and the model calls. The first real run makes 12 requests here. A later run finds 11 of them in the cache (`.cache/ekb/model-cache.sqlite`) and asks again only for `HR-PL-99`, whose reply was forgotten when the demo deleted it.
2. **One chunk, before and after:** `hr_docs:HR-PL-01#3`, its text, its trail, then its header and which model wrote it. With `--fake` the header is a placeholder (`fake-small chunk_3`): the fake has no language skill.
3. **Labels per page:** department and where it came from, chunks per sensitivity, then `HR-CMP-01` chunk by chunk, each `restricted: the document says “(restricted)”`.
4. **Near misses:** `hr_docs:HR-PL-04#2  says 'salary' -> internal`, then the same for `HR-PL-07#1` ('salario') and `HR-PL-10#2` ('confidential').
5. **Personal data:** dana creates `HR-PL-11` "Payroll contacts, Italy". Its contact chunk is `internal, people, pii: email, phone`; its IBAN chunk is `confidential, people, pii: iban`.
6. **A replay:** T4 again on all 13 pages, `0 requests, 13 from the cache`.
7. **Changes:** `HR-PL-04` edited → new header and labels; `HR-PL-01` saved unchanged → skip; `HR-PL-05`'s readers narrowed → acl_only, nothing re-labelled; `HR-PL-99` deleted → `chunks forgotten, and 1 cached model reply` (2 with `model_plus_rules`).
8. **Summary:** `Labelled chunks: 43 of 43. Publishable chunks: 0 of 43`. Every chunk has a sensitivity now, but no vector (T7) or ACL (T8).

Without keys and without `--fake`, the demo still runs: headers are `failed` with the reason, and it tells you how to set the keys (S7 walkthrough).

In the inspector (`uv run --env-file .env ekb inspector`, then `npm run dev` in `ui/`), open **T4 Chunk**: a sensitivity column in the list; click `hr_docs:HR-CMP-01` for four red `restricted` badges, each explaining why on hover, and each chunk's header above its text. The **Model gateway** tab shows the header calls. Add a page with an IBAN through the docs tool's `/docs` page and watch its chunk turn confidential.

## 8. Decisions and limits
- **Synthetic data goes to free tiers**, `HR-CMP-01` included (D15's POC assumption). Production uses Bedrock; with real data, restricted documents could also skip the model entirely.
- **Not all JSON schemas work everywhere.** The schema avoids `additionalProperties`, and the reply is read defensively, but the real providers' structured output was not called from the machine that built this step. The first real run is yours. A reply that parses but misses headers **is cached**: it stays until the document changes or the prompt version is bumped.
- **A failed header is not retried by itself.** The document goes on with empty headers; it gets them at its next edit, or when a reconciliation run (S16) re-processes documents whose `header_id` says `no header`.
- **One call per document per step**, not one for T4, T5 and T6 together (D16 #4, partly applied): the steps stay independent. Long documents send their text twice (the document and the passages), and nothing splits a document too long for the model's context yet.
- **The rules are a floor, not a classifier.** They catch explicit markers and identifiers with patterns; they don't understand that a paragraph about one person's salary is confidential. That is what the model, or Presidio, adds. Card 4.9's experiment (S18) measures false negatives on a labelled set.
- **Markers apply to the whole document.** A page that says "Do not forward" in one section is restricted everywhere. That over-labels in rare cases, on purpose.
- **Department is per document.** A chunk about expenses inside a people policy stays `people`.
- **The header is not in the text yet.** Nothing searches yet; S10 (embeddings) and S11 (the search store) put it in front of the text when indexing. Token counts (S6) don't include it.
- **The demo and the inspector choose options by hand**, as earlier steps did; the pipeline (S12) reads them from config.

## 9. Check your understanding
1. lucia narrows `HR-PL-05`'s readers to `es-employees` only. Does T5 re-label its chunks? Why is that correct, and what would go wrong if sensitivity depended on who may read a page?
2. `HR-CMP-01`'s "Review cycle" chunk says nothing secret on its own. Why is it `restricted`, and why does the code of conduct, which says shopper data "are confidential", stay `internal`?
3. dana deletes `HR-PL-99`. Its chunks are dropped as in S6. What else is dropped in S8, why does it matter, and what happens to a cached reply another live document also used?

<details>
<summary>Answers</summary>

1. No. T2 sees the same content hash with different readers and decides `acl_only`, the fast lane: T3, T4 and T5 don't run, so the header and labels stay as they were. That is correct because the labels come from the text, which didn't change. Who may read the page is T8's job (S11). If sensitivity depended on the ACL, the fast lane would leave a stale label (T5 never runs there), or every permission change would have to re-run T5 and its model call.
2. The restriction is written once, at the top ("Restricted to the compensation team. Do not forward." and "(restricted)" in the title). The rules treat a document marker as a floor for every chunk, because the sections below it are just as restricted; a missed restriction is a leak, an extra one only friction (card 4.9). The code of conduct only uses the word "confidential" in a sentence. The rules look for markers (a title tag, "Restricted to", "Do not forward", a line starting "Confidential:"), not topic words, so it stays `internal`.
3. Its **cached model replies**: T2's purge now calls `forgetter`, which drops the parsed text and chunks and then `gateway.forget("hr_docs:HR-PL-99")`. A header paraphrases the document's text, so keeping it would keep a readable trace of a deleted document (the demo shows `1 cached model reply`, 2 with `model_plus_rules`). A reply also tagged by another live document (the same call made for both) only loses this document's tag and is kept; it goes when its last document is deleted (S7's `forget`).
</details>
