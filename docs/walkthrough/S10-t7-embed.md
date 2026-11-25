# S10 · T7 Embed: what a vector is and why the model ID is stored

> Architecture: `docs/architecture.md` (box 4, "T7 · Embed"; transformations table: "Chunk → vector (model ID stored)"). Plan: `docs/plan.md` §4.2, S10. Option card: `docs/option-evaluation.md` 4.4 (embeddings: "multilingual and cross-lingual quality is the deciding factor"; "changing model later means re-embedding everything"). Decisions: D10 (the model ID is stored with every vector), D15 (every model call goes through the gateway), D16 #8 (embed once and reuse).

## 1. In one paragraph
A search for "can I move my vacation days?" should find the paragraph that says "You may carry up to 5 unused days into the next year", even though the two share almost no words, and even when the question is in Italian. Keyword search can't do that. So T7 turns each chunk into a **vector**: a fixed-length list of numbers (1024 for bge-m3), placed by the model so that texts with a similar meaning point in a similar direction, in any of the languages it knows. Search (S13) then compares the question's vector with the chunks' vectors. Every vector is stored with the **ID of what made it** (`model@1(input@1, ollama:bge-m3, 1024)`), because a vector only means something next to vectors from the same model: when the model changes, the IDs say exactly which chunks to embed again. The model is called through the model gateway like every other model, which caches each chunk's vector by its text, so an edit sends only the chunk that changed, and a delete forgets the document's vectors.

## 2. Where it sits in the flow
```
documents:  T2 ── process ──▶ T3 ──▶ T4 ──▶ T5 ──▶ T6 ──▶ [ T7 embed ] ──▶ T8 (S11)
                                                         1. text  = embed_input(chunk)        header + trail + text
                                                         2. vectors = embedder.embed(texts)   model option → gateway "embed" task
                                                         3. embedded(): one checked unit vector per chunk, with its ID

gateway.embed(task, texts, input_version, document_key)
            per text: cache hit? ── yes ──▶ vector from the cache (tagged with the document)
                                └─ no ───▶ one request with all the missing texts ──▶ ollama:bge-m3
T2 ── purge ──▶ forgetter ──▶ gateway.forget(document_key) ──▶ the document's vectors are dropped

input:  Chunk(…S6, S8, S9 fields…, sensitivity=internal, acl=None, vector=None)
output: Chunk(…, vector=(0.0123, -0.0456, … 1024 numbers, length 1),
              embedding_model="model@1(input@1, ollama:bge-m3, 1024)")          ← still not publishable: no ACL
```
T7 runs only where T2 decided `process`, like T3 to T6. A `skip` (same text) and an `acl_only` (only the readers changed) never embed again: a vector comes from the text, not from who may read it.

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/platform/gateway/{config,models,port,keys,errors,gateway,status}.py` | Embedding models (`kind: embedding`, `dimensions`); an embedding task names **exactly one** model; `EmbeddingClient` and `TextEmbedder` ports; `ModelGateway.embed()` with a per-text cache; `embed_key`, `batch_key`; `TaskKindError`; `CallRecord.items`; `provider_status(kind=)` |
| `src/ekb/platform/gateway/options/{fake,langchain}.py` | `FakeEmbeddingClient` (hash vectors, no meaning); `LangChainEmbeddingClient` with `EMBEDDING_PROVIDERS` (`OllamaEmbeddings`; `BedrockEmbeddings` for the production design) |
| `src/ekb/process/t7_embed/input.py` | `embed_input(chunk)` and `INPUT_VERSION = "input@1"`: what text of a chunk is embedded |
| `src/ekb/process/t7_embed/port.py` | `Embedder` (`model_id`, `dimensions`, `embed(texts, document_key=)`) and `Vectors` |
| `src/ekb/process/t7_embed/step.py` | `EmbedStep`, `EmbedResult`, `check_input()`, `embedded()`, `unit()`, `cosine()`, `vector_state()` |
| `src/ekb/process/t7_embed/options/{model,fake}.py` | `t7_embed: model` (the gateway's `embed` task) and `fake` (offline, now with an ID that says so) |
| `src/ekb/contracts/knowledge.py` | `Chunk.vector` and `Chunk.embedding_model` documented |
| `src/ekb/app/embed.py`, `app/chunk.py`, `app/gateway.py` | `embedder()`, `embed_step()`, `probe()`, `embed_help()`; T7 behind T6 in `chunk_after_t3(…, t7=)`, `Processed.vectors`; embedding clients built from the config |
| `src/ekb/app/demos/t7.py`, `demos/__init__.py` | `ekb demo t7` |
| `src/ekb/app/inspector.py`, `operate/inspector/{log,models}.py` | `ekb inspector --embed model\|fake` with a startup check; vector info on the chunk views; model kind and size on the gateway panel |
| `ui/src/components/{ChunkScreen,GatewayScreen}.tsx`, `chunk.ts`, `styles.css`; `ui/openapi.json`, `src/api/schema.d.ts` | Vector badge, model ID and first numbers on every chunk; a "vectors" column; texts per embedding call in the gateway's call list; the regenerated contract |
| `config/default.yaml`, `config/profiles/{ci,bedrock}.yaml`, `evaluation/experiments/4.04-embeddings.yaml` | `t7_embed: model`; `ollama:bge-m3` (1024) on the `embed` task; `fake:fake-embed` in `ci`; Cohere Embed v4 in `bedrock`; card 4.4's options as routes |
| `tests/unit/platform/gateway/test_embed.py`, `test_embedding_clients.py`, `test_config.py`, `helpers.py` | The embedding path, the clients, the settings rules |
| `tests/unit/process/t7_embed/*` | The input recipe, the step, the `model` and `fake` options |
| `tests/unit/app/test_embed.py`, `test_demos.py`, `test_inspector_command.py`, `test_gateway_wiring.py`; `tests/contract/test_ports.py`; `tests/unit/operate/inspector/*`; `ui/tests/chunk.test.mjs` | The wiring behind T2, the demo, the live loop, the ports, the API views, the UI helpers |

## 4. How it works

### What a vector is
A vector is a list of numbers, all of the same length for one model. No single number means anything; what carries meaning is the **direction** of the whole list. The model is trained so that texts saying similar things get vectors pointing in similar directions, and a multilingual model like bge-m3 does that across languages. Two vectors are compared by their **cosine**: 1 when they point the same way, about 0 when they are unrelated.
```python
def cosine(first, second):
    if len(first) != len(second):
        raise ValueError("vectors of different sizes cannot be compared")
    if not any(first) or not any(second):
        return 0.0
    return math.sumprod(unit(first, len(first)), unit(second, len(second)))
```
T7 scales Ollama and fake vectors to length 1, so the cosine of two stored vectors is just their dot product: that is what a vector index computes millions of times per second (S13, S17).

### What text is embedded
`embed_input(chunk)` joins three parts with blank lines, leaving out the empty ones:
```
From the Annual leave policy: carrying unused days over.   ← context header (T4, S8)
Annual leave policy > Carrying leave over                  ← section trail
You may carry up to 5 unused days into the next year ...   ← the chunk's text
```
The text alone often lacks the words a question uses; the header (when a model wrote one) and the trail put it back in its document. The nearest heading may appear twice, in the trail and at the top of the text, where T4 renders it; that does no harm. The recipe is versioned, `input@1`: it is part of every vector's ID and every cache key, so a new recipe makes every vector visibly out of date and reuses none.

### The step checks, the embedder only answers
`EmbedStep.handle(document, chunks)` builds one input per chunk, calls the embedder once with all of them and the document's key, then `embedded()`:
- exactly one vector per chunk, each of the embedder's size, every number finite, none all zeros; otherwise `EmbedError`;
- float vectors scaled to length 1, or signed int8 components preserved for Bedrock. The model ID includes `int8` for this encoding. Use cosine on the original int8 components.

Chunks from another document version are refused, and so are chunks that already have a vector (a new version is a new set of chunks) or an ACL (T7 runs before T8). After T7 a chunk has its sensitivity and its vector but **no ACL**, so `Chunk.publishable` is still false: T8 (S11) opens the gate.

### The `model` option and the gateway's embedding path
`ModelEmbedder` doesn't choose a model; the gateway's configuration does:
```yaml
models:
  "ollama:bge-m3": { kind: embedding, dimensions: 1024 }
tasks:
  embed: ["ollama:bge-m3"]   # exactly one: the settings refuse a second
```
An embedding task may name only one model, because vectors from two models cannot be compared: failing over to another model would silently split the index into vectors that never match each other. So the option knows the model and its size when it is built, and its ID names both: `model@1(input@1, ollama:bge-m3, 1024)`.

`ModelGateway.embed(task, texts, input_version=, document_key=)` works like `complete()` (route, reuse, guard, call, log), with two differences:
1. **Reuse is per text.** Each text has its own cache key (`embed_key`: model, its params, the input version and the text). The texts found are tagged with the document; only the missing ones are sent, each distinct text once, **in one request**. Re-processing an edited page sends only the chunk that changed; replaying the change log after a crash sends nothing.
2. **No failover.** If the one model is skipped (package missing, quota used, rate-limited) or fails, the call fails with `NoModelAvailableError`. A vector of the wrong size is a configuration error (`invalid`: the call stops).

The cost log gets one record for the hits and one per request, with `items` (how many texts), never the text or the vector. LangChain's embedding interface returns vectors only, so the record has no token counts; a free model costs $0.

### Fail closed, not open
A context header is an enrichment: T4 leaves it empty when no model answers (S8). A vector is one of the three things the publication gate needs, so T7 **raises** instead: nothing is kept, T2 records nothing, and the event comes back to be retried (S4's rule). Before they start, `ekb inspector` and `ekb demo t7` call `probe()`, one real embedding of a text no document holds, forgotten at once, so a stopped Ollama is reported up front instead of failing every page.

### Deletes
A vector can give away the text it came from, so it must not outlive a deleted document. Each cached vector is tagged with its document, and the purge already calls `gateway.forget(document_key)` (S8's `forgetter`), so the vectors go with the document's cached headers and labels. A vector another live document still uses (the same text in two pages) is kept.

### The model ID
`Chunk.embedding_model` holds the ID; `vector_state(chunk, current_id)` says `none`, `current` or `stale`. When the model, its size or the input recipe changes, every chunk whose ID differs from the current one must be embedded again before it is searched with the new model. The IDs make that a planned batch job (replay the originals, S3), and the cache key holds the model too, so no old vector is reused by mistake.

### The inspector and the UI
`ekb inspector` runs T7 behind T6 (`--embed model`, the default, or `--embed fake`) and stops at startup if the model cannot answer. `GET /api/t4/chunks/document` gains, per chunk, `embedding_model`, `dimensions`, the first eight numbers (`vector_preview`) and `vector_state`; the list gains each document's embedder, T7's note and how many chunks have a vector. The gateway panel shows each model's kind and size, and each call how many texts it covered. On the **T4 Chunk** tab every chunk shows a vector badge ("1024 numbers", or "stale: embed again"), its model ID and its first numbers.

## 5. Options
Built now, chosen by hand in `ekb.app.embed` (choosing from config comes with the pipeline, S12):
- `t7_embed: model`, through the gateway's `embed` task. Locally `ollama:bge-m3` (`config/default.yaml`, free, runs on the laptop, nothing leaves it); in the `bedrock` profile `bedrock:cohere.embed-v4:0` at 1024 dimensions (D10; not run in the POC).
- `t7_embed: fake`: the `ci` profile's choice and `ekb inspector --embed fake`; deterministic hash vectors with no meaning, no gateway.
- In the gateway: `embedding_client: langchain` (Ollama, Bedrock) and `fake` (`fake:fake-embed`, the `ci` profile's `embed` task and `ekb demo t7 --fake`).

Swapping the model is configuration, not a new option: one line in `tasks: embed`, and a declared model with its `dimensions`. Card 4.4 compares bge-m3, Cohere Embed v4 (float and int8) and Titan Text v2 that way (`evaluation/experiments/4.04-embeddings.yaml`, S18). Gemini's `gemini-embedding-001` on the AI Studio free tier is the hosted alternative if the laptop cannot run bge-m3; it needs an `EMBEDDING_PROVIDERS` line and an experiment result before it becomes the default.

## 6. What the tests prove
- **The gateway's embedding path:** each text is embedded once, then comes from the cache; an edit sends only the texts that changed; the same text twice in one call is sent once; vectors come back one per text, in order, of the declared size. The input version is part of every key and required. A cached vector of another size is not reused. Wrong-kind tasks are refused both ways. An unavailable model fails the call and nothing is cached; a rate-limited one rests its quota; an invalid request stops; vectors of the wrong size are refused; a model without its package or past its quota is skipped without a request, while the cache still answers. **Deletion gate:** a deleted document's vectors are forgotten unless another document uses the same text. The cost log never holds a text or a vector.
- **The settings:** an embedding model must declare its dimensions, a chat model must not; a task cannot mix kinds; an embedding task naming two models is refused. The local profile embeds with `ollama:bge-m3` (1024) and nothing else; `ci` uses only fake models; `bedrock` routes `embed` to Cohere Embed v4.
- **The clients:** the fake returns unit vectors and fails as scripted; Ollama gets its model, URL and timeout and reports no token counts; Bedrock names its model `model_id`; a missing package says what to install; "Ollama not running", "model not pulled" and timeouts become `unavailable`, a bad input `invalid`, and no provider message (which can echo text) is kept.
- **The T7 step:** every locally embedded chunk gets a unit vector and the ID that made it, and nothing else changes; the embedder is sent each chunk's input and the document's key. **Publication gate:** a chunk with a sensitivity and a vector but no ACL is not publishable; adding the ACL is what makes it so. A wrong count, wrong size, NaN or zero vector is refused; chunks from another document or already past T7 or T8 are refused; no chunks, no call. A vector made by another model, size or recipe is stale.
- **The `model` option:** its ID names the recipe, the one model and its size, known before any call; a document embedded twice comes from the cache the second time; an edit sends one chunk; a failing model fails the step and caches nothing; purging the document empties the cache.
- **Behind T2:** every chunk leaves with a vector and its ID, unpublishable; a skip and the fast lane never embed again; an edit sends only the changed chunk; a purge forgets the vectors; if the model is down nothing is kept, T2 records nothing and the event comes back, and once the model is back the page is processed. The probe never answers from the cache and leaves nothing in it.
- **The live loop and the CLI:** the loop embeds every chunk through the gateway and a deleted page's vectors go; without a gateway the `model` option is refused; the command stops with instructions when the model cannot answer; an unknown `--embed` is refused.
- **API and UI:** the chunk view carries the model ID, size, preview and state; the contract is regenerated; the UI helpers label and preview vectors.
- **The demo:** the whole story runs offline on the fake model and says so; with no model it falls back to the fake.

## 7. Run it yourself
**Once: install Ollama and bge-m3.** Install Ollama for Windows from ollama.com, then in a terminal:
```powershell
ollama pull bge-m3          # 1.2 GB, once
uv sync --extra ollama      # LangChain's Ollama integration (optional extra)
```
In `.env`, set `OLLAMA_URL=http://localhost:11434` (the `http://ollama:11434` in `.env.example` is the Docker service's name, which works only inside the Compose network). Running Ollama in Docker instead works the same way: `docker compose --profile models up -d`, then `docker compose exec ollama ollama pull bge-m3`; the port is published on `127.0.0.1:11434`.

```powershell
uv run --extra ollama --env-file .env ekb demo t7
uv run ekb demo t7 --fake            # offline: same plumbing, numbers with no meaning
```
The first call loads the model into memory, which can take a few seconds on integrated graphics. You should see:
1. **Similar and different sentences:** the same question in English (A), Italian (B) and Spanish (C), then one about expenses (D) and one about office hours (E), and a table of cosines. With bge-m3, B and C should be the closest to A and D and E clearly further. Read the order, not the value: unrelated texts rarely score 0. With `--fake`, every pair is close to 0, and the demo says why.
2. **Initial load:** one line per page (`HR-PL-01   4 chunks  4 sent, 0 from the cache`), then `48 of 48 chunks from 12 pages have a vector`.
3. **One chunk and its vector:** `hr_docs:HR-PL-01#3` ("Carrying leave over"): the exact text T7 sent, the first numbers of its vector, `length 1.000`, its `embedding_model`, and `publishable: False` (no ACL).
4. **Changes:** dana changes "5 unused days" to "7": `HR-PL-01 v2.1: T2 process -> T3-T7: 1 chunk(s) sent, 3 from the cache`. lucia changes `HR-PL-05`'s readers: `acl_only`, nothing embedded. dana deletes `HR-PL-99`: `4 cached vectors forgotten`.
5. **Why the model ID is stored:** the chunk's ID is `current` against today's model and `stale` against Cohere Embed v4.
6. **Summary:** T7 ran once per `process`; requests, texts sent and from the cache, $0; `Publishable chunks: 0 of 44`.

If Ollama is not running, the demo says why and runs on the fake model. The inspector instead stops: start Ollama, or run `uv run ekb inspector --header none --embed fake`. With Ollama up, `uv run --extra ollama --env-file .env ekb inspector`, then `npm run dev` in `ui/`, and open **T4 Chunk**: every chunk shows `1024 numbers`, its model ID and its first numbers. Edit one paragraph of a page in the docs tool's `/docs` page and watch the **Model gateway** tab: one `embed` request for the changed chunk, and one hit covering the page's other chunks (`×3` for a four-chunk page).

## 8. Decisions and limits
- **Settled in S10's plan (25 November 2026):**
  - the local default is bge-m3 on Ollama, as `config/default.yaml` and the plan already said ([local experiment result](../../evaluation/results/2026-11-25-4.4-local-embeddings.md)); D15's "decided in S10" row now says so;
  - embeddings go through the gateway, with a per-text cache tagged by document and a cost-log record per request;
  - an embedding task names exactly one model: no failover;
  - T7 fails closed; the header still fails open;
  - the input is header + section trail + text, versioned `input@1`, and the ID names option, recipe, model and size;
  - the option is `model` and the model is the gateway's configuration (`bge_m3_ollama` and `cohere_embed_v4` are gone from the config and from experiment 4.04);
  - Ollama vectors are normalized floats. Bedrock stores 1024 signed int8 components with the encoding in the model ID;
  - T4 keeps its approximate token counter: 400-token chunks are far below bge-m3's 8,192;
  - the demo falls back to the fake model; the inspector stops with instructions.
- **Dense vectors only.** bge-m3 can also produce sparse and multi-vector output; Ollama serves the dense vector, and keyword matching is BM25's job in the search index (S13).
- **No token counts.** LangChain's embedding interface returns vectors only, so embedding records show 0 tokens. That is right for free models. A paid embedding model (Cohere on Bedrock) would log an unknown cost, and the gateway pauses paid calls while a cost is unknown (S7), so the `bedrock` profile needs token counting before it is run; the POC never runs it.
- **One request per document.** All of a document's new texts go in one gateway request and count once against a quota. The Bedrock int8 adapter splits more than 96 texts into provider batches. A gateway log row counts the logical request.
- **Vectors are cached as JSON** in the gateway's SQLite cache, about 20 KB per chunk at 1024 dimensions: fine for the POC's few hundred chunks. Production keeps them in the search index (D16 #8) and as int8 (card 4.4).
- **Questions are not embedded yet.** S13 embeds them through the same `embed` task. bge-m3 needs no query prefix; Cohere distinguishes documents from queries (`input_type`), which the Bedrock route will need then.
- **"Stale" shows only after a model change.** In one inspector run every vector comes from the same model; the state is there for a switch and for a rebuild.
- **The probe is one real call** each time the inspector or the demo starts, and it is in the cost log.
- **The demo and the inspector choose options by hand**, as earlier steps did; the pipeline (S12) reads them from config.

## 9. Check your understanding
1. dana changes one sentence in `HR-PL-01`'s "Carrying leave over" section. How many texts reach the model, and why? And if she changes the page's top heading instead (`# Annual leave policy` → `# Holiday policy`)?
2. T4's context header is left empty when no model answers, but T7 raises and the event comes back. Why the difference? And why may the `embed` task name only one model, when every chat task has four?
3. Production switches from bge-m3 to Cohere Embed v4, also at 1024 numbers. What do the stored IDs let you do, will any cached bge-m3 vector be reused, and why can't a bge-m3 vector simply be compared with a Cohere one?

<details>
<summary>Answers</summary>

1. One. T2 sees new text and decides `process`; T3 to T6 run again and T7 builds the four inputs. Three are exactly what was embedded before, so their keys are found in the gateway's cache (tagged with the document); only the changed chunk's input is sent, in one request. The top heading starts the **section trail** of every chunk (T3 builds the trail from the headings above each passage), and the trail is part of every input, so all four inputs change and all four are sent. Same rule: a text is reused only if it is exactly the same text, recipe and model.
2. The header is an enrichment: a chunk without one is still correct, just a little harder to find, and the publication gate does not need it. A vector is one of the three things the gate needs (sensitivity, vector, ACL), so a chunk without one can never be searched; moving on would leave a page silently missing from search. Raising keeps nothing, T2 records nothing, and the event is retried. One model only, because vectors from two models live in different spaces: if a quota sent half the pages to another model, those pages would never match a question embedded with the first. Chat failover is safe because each answer stands on its own.
3. Every chunk whose `embedding_model` is not the new ID is stale, so the IDs list exactly which chunks to embed again: a planned batch from the stored originals (S3), not a guess. No bge-m3 vector is reused: the cache key holds the model (`ollama:bge-m3` vs `bedrock:cohere.embed-v4:0`), so every text is a miss under Cohere. The two models were trained separately and put meaning in unrelated directions; having 1024 numbers each makes the dot product computable but meaningless, so vectors are only ever compared with vectors of the same ID.
</details>
