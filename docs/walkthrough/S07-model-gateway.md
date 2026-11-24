# S07 · Model gateway: every model call routed, reused, guarded and costed

> Architecture: `docs/architecture.md` (box 4, the model steps T4–T6, and Q1); `AGENTS.md`, hard rules ("every model call goes through the platform model gateway"). Plan: `docs/plan.md` §4.2, S7. Decisions: D15 (models per task, revised in S7), D16 #1 (content-hash cache), #7 (capped output), #14 (spend guard). Option cards that will compare models through it: 4.3 (context header), 4.8 (query planning), 4.9 (classification), 4.10 (entities), 4.11 (answers), 4.12 (judge).

## 1. In one paragraph
From S8 on, several steps call language models: T4 writes a context header, T5 classifies, T6 extracts entities, Q1 plans queries, the assistant answers and the judge grades. None of them calls a model directly. They name a **task**, and the gateway handles the rest:
- **Which model.** Each task has an ordered list of candidates across four free providers.
- **Whether a reply already exists** for exactly this call (the content-hash cache).
- **Whether a model may be called now:** is its key set, is free quota left today, did it answer "too many requests" recently, has logged spend reached the cap, or is a paid charge awaiting reconciliation?
- **What gets written down:** one record per call, with tokens, cost and outcome, and never the text.

LangChain makes the actual call. A new provider is one package away, and a new model is one line of config. The demo spends about five requests of your free quotas. It shows:
- a first call (a miss) and the same call again (a hit)
- three changes that each make a new call: a new prompt version, an edited passage, another task
- a failover
- a restart that still finds the cache

## 2. Where it sits in the flow
```
T4 header (S8), T5, T6, Q1, the assistant, the judge
      │  gateway.complete(task, prompt, prompt_version=…, max_output_tokens=…, document_key=…)
      ▼
[ Model gateway ]                                   ekb.platform.gateway
   1. route   task → candidates, in order          config/default.yaml › gateway.tasks
   2. reuse   cache.get(key) under every candidate ── hit ──▶ reply, cost $0, no request
   3. guard   key set? quota left today? rate-limited lately? spend cap?  no → next candidate
   4. call    ModelClient.invoke(request) ── LangChain ──▶ Google AI Studio · Groq · Mistral · OpenRouter
   5. log     CostLog.append(record)        every hit and every attempt; never the text
      ▼
GatewayReply(text, candidate, served_model, cached, input_tokens, output_tokens,
             cost_usd, latency_ms, key, steps)
```
It is plumbing in `ekb.platform`, not a pipeline step. Every layer may use it, and it uses none of them. Nothing in the pipeline calls it yet: S8 is the first caller.

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/platform/gateway/config.py` | `GatewayConfig`: `providers`, `quotas`, `models`, `tasks`, the cache and cost-log choices, `max_spend_usd`, `timeout_s`, `max_wait_s`, `cooldown_s`. Every reference is checked when the settings load. |
| `src/ekb/platform/gateway/models.py` | `ChatRequest`, `ModelReply`, `CachedReply`, `CallRecord` (the cost log line, with no text), `Step` and `GatewayReply` |
| `src/ekb/platform/gateway/port.py` | Three ports: `ModelClient`, `ResponseCache` (with `tag` and `forget`) and `CostLog` |
| `src/ekb/platform/gateway/keys.py` | `cache_key()`: a SHA-256 of the candidate, its params, the prompt version and the request |
| `src/ekb/platform/gateway/usage.py` | Quota windows from the cost log (`day_start` and `next_reset` in a time zone), `quota_usage`, `spent_usd`, `totals` |
| `src/ekb/platform/gateway/errors.py` | `ModelCallError` (rate_limited, unavailable or invalid), `NoModelAvailableError`, `InvalidModelRequestError`, `UnknownTaskError` |
| `src/ekb/platform/gateway/gateway.py` | `ModelGateway.complete()`: route → reuse → guard → call → log; also `forget()` and `usage()` |
| `src/ekb/platform/gateway/status.py` | `provider_status()`: is each provider's key set? It never returns the key. |
| `src/ekb/platform/gateway/options/fake.py` | `fake` client: offline and deterministic, with scripted failures |
| `src/ekb/platform/gateway/options/langchain.py` | `langchain` client: the `PROVIDERS` table, `classify()` and `describe()` for errors. The only module that imports LangChain. |
| `src/ekb/platform/gateway/options/cache_{memory,sqlite}.py` | `in_memory` and `sqlite` caches, with per-document tags |
| `src/ekb/platform/gateway/options/log_{memory,jsonl}.py` | `in_memory` and `jsonl` cost logs |
| `src/ekb/platform/config.py` | `Settings.gateway` replaces `provider`. A profile that picks another option no longer inherits the old option's params. |
| `config/default.yaml`, `profiles/ci.yaml`, `profiles/bedrock.yaml` | The free-tier chains (local), the fake chain (ci), the production design (bedrock) |
| `src/ekb/app/gateway.py` | `build_gateway()`, `build_client()`, `max_spend()` and `openrouter_free_requests()`. The only place concrete options are chosen. |
| `src/ekb/app/demos/gateway.py` | `ekb demo gateway` (and `--fake`) |
| `src/ekb/app/cli.py` | `ekb options` also lists the gateway: the cache, the log, every provider's key status, every task's chain |
| `src/ekb/app/inspector.py`, `src/ekb/operate/inspector/{gateway,models,app}.py` | `GatewayPanel`; `GET /api/gateway` and `GET /api/gateway/calls` |
| `ui/src/components/GatewayScreen.tsx`, `ui/src/gateway.ts`, `App.tsx`, `api/client.ts`, `styles.css` | The **Model gateway** tab |
| `ui/openapi.json`, `ui/src/api/schema.d.ts`, `ui/package.json` | Regenerated contract; the new UI test file |
| `pyproject.toml`, `uv.lock` | `langchain-core`, `langchain-google-genai`, `langchain-groq`, `langchain-mistralai`, `langchain-openrouter`, `tzdata`; the `ollama` extra; the import-linter contract that keeps LangChain in one module |
| `.env.example` | The four key variables, empty |
| `tests/unit/platform/gateway/*` | The gateway, config, both clients, both caches and logs, usage and keys |
| `tests/unit/app/test_gateway_{wiring,demo}.py`, `tests/unit/operate/inspector/test_gateway_panel.py` | Wiring, the demo, the panel |
| `tests/http.py` + small edits to six existing test files | `as_httpx()`: since S7, Starlette's `TestClient` is an `httpx2` client (see §8) |
| `tests/contract/test_ports.py`, `tests/unit/test_config.py`, `tests/unit/test_cli.py` | The three new ports; the config merge rule |
| `ui/tests/gateway.test.mjs` | Quota bars, labels, money, the `provider:model` split, newest first |

## 4. How it works

**Route: tasks, not models.** The configuration names each task's candidates in order. The high-volume tasks start on the light models, and the harder ones start on the stronger models of the same providers:
```yaml
tasks:
  context_header: &light
    - "google_genai:gemini-3.5-flash-lite"
    - "groq:openai/gpt-oss-20b"
    - "mistralai:mistral-small-latest"
    - "openrouter:openrouter/free"
  entities_hard: &strong
    - "google_genai:gemini-3.8-flash"
    - "groq:openai/gpt-oss-120b"
    - "mistralai:mistral-large-latest"
    - "openrouter:openrouter/free"
```
A model is named `provider:model`, and only the first `:` separates the two, because some model IDs contain one (`ollama:gemma4:e4b`). Every model a task names must be declared under `models:` with its quota and price, and every provider under `providers:`. A typo fails when the settings load, not on the first call. An unknown task is an error, never a silent default.

**Reuse: the cache key.** Two calls are "the same call" when everything that shapes the answer is the same:
```python
payload = {
    "key_version": KEY_VERSION,
    "candidate": candidate,  # another model gives another answer
    "params": dict(params),  # reasoning_effort: low changes it too
    "prompt_version": prompt_version,  # changes the text does not show
    "request": request.model_dump(mode="json"),  # system, prompt, limit, temperature, schema
}
```
The request's text (system prompt and prompt) is already in the key, so a step that rewords its instructions gets a new key by itself. The **prompt version** covers what the text does not show. The step may start reading the reply differently (a new field, a fixed parser), or you may want fresh answers on purpose. Bumping `header@1` to `header@2` retires every old reply at once, even though the same text is sent. The version is also the only label in the cost log saying which prompt a call used, because the log keeps no text.

The cache is asked under **every** candidate's key, in order, before any request. A reply Mistral wrote yesterday, when Google's quota was used up, is reused today even though Google is back. The answer is already paid for, in quota.

**Guard: skip without a request when the answer is known to be no.** In `_blocked()`, in this order:
1. **No key:** `client.check()` says `GROQ_API_KEY is not set` (or that a package is missing).
2. **Spend cap:** for a paid model only, the cost log's total has reached `EKB_MAX_SPEND_USD` (default 25).
3. **Daily quota:** today's requests against the model's quota have reached `per_day`. Today starts at midnight in the quota's own time zone: Pacific time for Google, UTC for the others.
4. **Cooldown:** the provider said "rate limited" less than `cooldown_s` (600) ago.
5. **Per-minute limit:** if the next slot is at most `max_wait_s` (20 s) away, the call waits for it, because staying on the same model beats switching. If it is further away, the call moves to the next candidate.

A skipped candidate is listed in the reply's `steps` with its reason (`skipped: daily quota groq_gpt_oss_20b used (1000/1000), resets 2026-11-25 00:00 UTC`). It is not logged, because no request was made.

**Call: three kinds of failure.** A client turns its library's errors into `ModelCallError` of one kind, and the gateway acts on the kind:

| Kind | Typical cause | The gateway |
|---|---|---|
| `rate_limited` | HTTP 429, `RESOURCE_EXHAUSTED` | logs the attempt, tries the next candidate, rests that quota for `cooldown_s` |
| `unavailable` | 5xx, timeout, bad key (401/403), no credits (402), model gone (404), a reply that broke the JSON schema | logs it, tries the next candidate |
| `invalid` | HTTP 400/422, context too long, an unknown argument | logs it and **stops** (`InvalidModelRequestError`): another model would reject the same request; it is a bug to fix |

`classify()` reads LangChain's standard errors first: Google and Mistral raise `ModelRateLimitError` and its siblings. Next it reads the HTTP status that Groq's and OpenRouter's SDK errors carry, and network errors last. Anything else counts as `invalid`, so a bug of ours is never hidden by quietly moving to the next model. Failed calls are never cached.

**Log: numbers, never text.** One JSON line per hit or attempt, in `.cache/ekb/model-calls.jsonl`:
```json
{"at":"2026-11-24T21:40:03Z","task":"context_header","candidate":"groq:openai/gpt-oss-20b",
 "prompt_version":"demo-header@1","key":"cda753eb683b…","outcome":"ok","quota":"groq_gpt_oss_20b",
 "input_tokens":96,"output_tokens":31,"cost_usd":0.0,"latency_ms":412,
 "document_key":"hr_docs:HR-PL-01","error":null}
```
Quotas, the spend and the inspector's totals are all computed from this file (`usage.py`). It is the only record, so the demo, the inspector and, later, the pipeline agree on the numbers, and a restart forgets nothing. Failed attempts count against quotas, because OpenRouter counts them too. Cache hits do not, because no request was made. Without text, the file can be shown in the inspector and kept for the budget without keeping a word of any document, deleted or restricted.

**Concurrent workers.** Each call holds the cache guard and then the cost-log guard until it has written both the cost record and the reply. File-backed stores use OS file locks associated with their paths. Two demos with separate caches still serialize access to a shared quota. A purge takes the cache guard, so when it returns an earlier in-flight call has finished writing. Workers sharing either store must use the same underlying path.

**Unknown charges.** A provider can return a paid answer without token counts. Its cost is recorded as JSON `null` and the inspector shows “Pending reconciliation”. Other paid calls pause across restarts. Free candidates and cache hits still work. To resume, stop writers, match the call to the provider’s usage report and replace its null cost in the JSONL record with the verified USD charge. A reply rejected by schema validation still contributes its reported tokens and cost. Provider diagnostics retain the error type and HTTP status.

**Deletes: tags on cached replies.** A cached reply is derived from document text. A context header of `HR-CMP-01` paraphrases the salary bands. A call made "about" a document passes `document_key`, and the cache tags the reply with it. A hit from another document adds that document's tag too. `gateway.forget(document_key)` drops the deleted document's tag and every reply left with no tag. A reply another live document still uses is kept. A reply never tied to a document is left alone. S8, the first per-document caller, wires `forget` to T2's purge.

**LangChain, in one module.** `options/langchain.py` maps each provider to its chat-model class and to how that class names the common settings:
```python
PROVIDERS = {
    "google_genai": ProviderClass("langchain_google_genai", "ChatGoogleGenerativeAI", ...),
    "groq": ProviderClass("langchain_groq", "ChatGroq", ...),
    "mistralai": ProviderClass("langchain_mistralai", "ChatMistralAI", ...),
    "openrouter": ProviderClass("langchain_openrouter", "ChatOpenRouter", ..., timeout_unit=1000.0),
    "ollama": ProviderClass("langchain_ollama", "ChatOllama", ..., max_tokens="num_predict"),
    "bedrock_converse": ProviderClass("langchain_aws", "ChatBedrockConverse", ...),
}
```
Each call builds the model with `model`, the configured `params`, `max_tokens`, `temperature`, `api_key`, a timeout and **`max_retries=0`**. A LangChain retry would spend a free request the gateway did not count, and choosing another model is the gateway's job. Watch the details: OpenRouter's timeout is in milliseconds, and Ollama calls its output limit `num_predict`. Token counts come from LangChain's `usage_metadata`, which is the same for every provider. With a `json_schema`, the client uses `with_structured_output(schema, method="json_schema", include_raw=True)`, so the tokens of the raw reply are still counted. An import-linter contract fails the build if any other module imports `langchain_core`. The provider packages are loaded by name, and only here.

**The inspector.** `ekb inspector` reads the same cost log. `GET /api/gateway` returns the providers (key set or not), the routes, the models with their prices and quotas, each quota's use today and its next reset, and the spend against the cap. `GET /api/gateway/calls?since=` returns the log's records with a cursor, plus totals per task and model. The **Model gateway** tab shows:
- the calls, newest first, with an outcome badge each;
- a bar per free quota;
- each task's chain, with the models whose key is missing struck through;
- the totals per task and model.

Neither endpoint can return a prompt, a reply or a key: they are not in anything it reads.

## 5. Options
Built now, chosen in `config/*.yaml` under `gateway:`:
- **Clients** (`model_client`): `langchain` for every real provider and `fake` for tests, the `ci` profile and `--fake`. Which provider and model is a candidate string, not an option: `google_genai:…`, `groq:…`, `mistralai:…`, `openrouter:…`. `ollama:…` works after `uv sync --extra ollama` with `OLLAMA_URL` set.
- **Caches** (`model_cache`): `sqlite` (local, `.cache/ekb/model-cache.sqlite`) and `in_memory` (`ci`).
- **Cost logs** (`cost_log`): `jsonl` (local, `.cache/ekb/model-calls.jsonl`) and `in_memory` (`ci`).
- **Profiles:** `local` (the default) uses the four free tiers. `ci` uses two fake models. `bedrock` is the production design: Haiku 5.5 through the EU profile, Sonnet and Opus through the global one, priced from D15. It needs the `aws` extra (`langchain-aws`), which is not installed in the POC.

Declared but not in any chain, each one line away in `tasks`: `openrouter:anthropic/claude-haiku-5.5` (paid, $0.10 / $0.50 per million tokens, so the spend cap applies) and `ollama:gemma4:e4b`.

Later, on the same gateway:
- `embed()` for T7 (S10). The `t7_embed: bge_m3_ollama` default needs an API model now that there are no local models.
- Prompt caching of the document prefix (D16 #2, S8).
- Batch inference (D16 #3, S18).
- More free providers: Cohere's trial key for embeddings and reranking, Cerebras (now credit-based), Groq's other models.

## 6. What the tests prove
- **The cache:** the same call twice reaches the model once, and the second reply is a hit at $0. A change to any part of the call (prompt version, one character, system prompt, output limit, temperature, schema) makes a new key and a new request. A reply cached under any candidate is reused. Failed calls are never cached.
- **Routing:** each task uses its own chain, and an unknown task is an error.
- **Failover:**
  - A used-up daily quota moves the call to the next model without a request.
  - The daily quota resets at midnight in its own time zone (tested across midnight in Los Angeles).
  - A 429 moves on and rests that quota until the cooldown ends.
  - An outage moves on without resting.
  - An invalid request stops, and the next model is never called.
  - A model without its key is skipped and not logged.
  - When every model is out, the error lists each one's reason and the first reset.
- **Per minute:** a 5-second wait for a slot is waited out on the same model; a 60-second one moves to the next.
- **The spend cap:** a paid model costs its price per million tokens. Past the cap, paid models are skipped while free ones still answer, and cached replies are never blocked.
- **No text in the log:**
  - The prompt, the system prompt and the reply appear in no record, and every key is a 64-character hash.
  - The panel's JSON has no prompt, reply or key value; only the key's variable name.
  - `ProviderStatus` never holds a key.
- **Deletes:** `forget()` keeps a reply another document still uses, drops it once the last one is deleted, and leaves untagged replies alone. Tested on both caches, and through the gateway.
- **Persistence:** the SQLite cache and its tags survive a restart. Two JSONL logs on one file see each other's records. A line cut short by a crash is skipped and counted, and the next record starts on a fresh line. Lines end in LF only.
- **LangChain:**
  - Replies, token counts and the served model come back from a LangChain chat model.
  - The system and user messages are sent in order.
  - The arguments are checked per provider: retries off, OpenRouter's timeout in milliseconds, Ollama's `num_predict` and `base_url`.
  - JSON goes through structured output, and a reply that breaks the schema is `unavailable`.
  - A missing key or package is reported before any call.
  - Seventeen real SDK errors (Groq, OpenRouter, Mistral, Google and LangChain's own) are sorted into the right kind.
  - An error's detail never carries the prompt.
- **Config:** invalid references, unpriced remote models without a free-tier quota, and non-finite spend caps fail at load. The local profile routes all seven D15 tasks across at least three free providers, every model with a quota and none paid. The `ci` profile only reaches fake models. `bedrock` is the production design. No key is in any config file.
- **Wiring:** the `ci` gateway answers every task offline. The local one builds a LangChain client per model, with the key from the environment. With no key at all, nothing is sent. `EKB_MAX_SPEND_USD` overrides the cap and is validated. OpenRouter's counter is read with the key as a bearer token, and is `None` when it can't be read. `ekb options` shows the gateway.
- **The demo:**
  - The story has 7 records, 5 requests and 2 hits.
  - It is identical on every run, and its requests count against a kept log across runs.
  - With a single model, the failover explains why the call fails.
  - OpenRouter's counter is shown, and so is a counter that cannot be read.
  - With no key, the error names the keys and says how to set them. The command exits 1 with that message.
- **The panel:** GET only (the existing all-GET test covers the new routes). It shows routes, prices, quotas used today (hits excluded) and a cursor over the calls, filtered by task. It is empty, not broken, without a gateway. The UI contract is current.

## 7. Run it yourself

### Get the free API keys (once, about 20 minutes)
Never paste a key in a chat and never commit one: keys go only in `.env`, which git ignores. A key you skip just removes that provider from every chain.
1. **Google AI Studio.** Go to aistudio.google.com and sign in. New users get a default project and key after accepting the terms; otherwise open **API Keys → Create API key** and choose a project. Your real limits are on AI Studio's rate-limit page. They count per project, so a second key in the same project adds nothing.
2. **Groq.** Go to console.groq.com and sign up with email, Google or GitHub. Open **API Keys → Create API Key** and copy the key: it is shown only once. No card is needed. Limits count per organization.
3. **Mistral.** Go to console.mistral.ai, sign up, choose the free plan and verify your phone by SMS (one phone number per plan). Then open **API Keys → Create new key**.
4. **OpenRouter.** Go to openrouter.ai and sign in, then open **Settings → Keys → Create key**. Don't add credits, so the key cannot spend money. In **Settings → Privacy**, turn on "Enable free endpoints that may train on inputs"; otherwise some free models are unreachable.
5. In the repository root, copy `.env.example` to `.env` and fill in `GOOGLE_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY` and `OPENROUTER_API_KEY`.

Then set the starting quota values in `config/default.yaml` (`gateway.quotas`) to the limits your consoles show. The Google values in particular are a guess.

### Run
```powershell
uv run --env-file .env ekb options        # the chains, and which keys are set
uv run --env-file .env ekb demo gateway   # about 5 requests of your free quotas
uv run ekb demo gateway --fake            # the same story offline; nothing kept
```
You should see:
1. **Who can answer:** each provider with `set` or `NOT set: skipped`, and each free quota's use today with its next reset, in UTC and your local time. With an OpenRouter key, you also see OpenRouter's own counter of free requests today.
2. **The routes** of `context_header` and `answer`.
3. **A context header** for "Annual leave policy > Carrying leave over": the model that answered, its latency, tokens in and out, cost `$0.000000`, the start of the key and the reply. Any candidate skipped on the way is listed first with its reason.
4. **The same call again:** `from the cache, no request`, with the same key and the same text.
5. **Only the prompt version** goes from @1 to @2, with the same text sent: a new key and a request.
6. **One character changes** (`31 March` → `31 march`): a new key and a request.
7. **The `answer` task** on its own, stronger chain.
8. **Failover:** a gateway where the first ready model's daily cap is 0 skips it (`skipped: daily quota … used`) and the next ready model answers. With only one key there is no next model, and the error says why.
9. **A new gateway on the same cache file** asks scene 3 again: a hit.
10. **The cost log:** `7 records this run, 5 requests, 2 hits`, then totals per task and model, the quotas after the run, the spend against the $25 cap, and where the log is kept.

With `--fake`, every call goes to the two fake models. Scene 8 moves from `fake-small` to `fake-large`, and the log is thrown away with the run. A real run's replies, latencies and the models that answer depend on your keys and on what each provider has left.

In the inspector, run `uv run --env-file .env ekb inspector`, then `npm run dev` in `ui/`, and open **Model gateway**:
- every call the demo made, newest first, with hits greyed out;
- a bar per free quota;
- the chains, with models struck through where the key is missing;
- the totals.

Run the demo again and watch the counts grow. The second run's scene 3 is a new request, because each run starts with a fresh cache.

## 8. Decisions and limits
- **Free tiers in development and in the POC's production** (D15, with the assumption in plan §4.2): the data is synthetic and assumed fine to send. The production design keeps Bedrock. OpenRouter and the other free tiers are an exception to "AWS or open source only", for the POC only.
- **LangChain makes the calls, the gateway decides.** We use LangChain's provider packages but neither its cache nor its retries (§4), and not `init_chat_model`, which pulls in LangGraph. LiteLLM was rejected (D15).
- **The default changed without an experiment:** from `ollama` to the free tiers, for lack of a local GPU, not for quality. This is recorded in D15.
- **Starting quota values are partly guesses.** Google's free limits are per project and per model and are shown only in AI Studio. Only requests are counted, not tokens per minute (Groq's free tier also caps those).
- **Model IDs move.** Gemini 2.5 was shut down in October 2026, and OpenRouter's free list rotates. `openrouter/free` picks a free model per request, so the same call may be answered by different models; the log records which one served it (`served_model`).
- **Failover changes the answering model.** One document's headers may come from different models on different days. Experiments (S18) must pin one model with no fallback.
- **Simple rules:** a fixed 10-minute cooldown after a 429, waits of up to 20 s inside the call, and one lock per gateway (calls run one at a time).
- **Thinking models** count their thinking as output. The demo allows 400 output tokens, so an empty reply means the model spent them all on thinking.
- **The cache keeps reply text.** `forget(document_key)` exists and is tested; S8 connects it to T2's purge. The cache and the cost log grow without limit, which is fine at POC size.
- **A side effect on the tests.** LangChain's dependencies (google-genai, langsmith, huggingface-hub) install `httpx2`, and Starlette's `TestClient` now uses it. The product's `httpx` code works unchanged against it, and `tests/http.py` documents the type cast. But an error raised through a `TestClient` is an `httpx2` error, which `except httpx.HTTPError` does not catch. To test HTTP error handling, use `httpx.MockTransport`.
- **LangSmith** is installed with `langchain-core`, and its tracing stays off unless `LANGSMITH_TRACING` is set. Never set it: it would send every prompt to LangSmith (`.env.example` says so).
- **No real call in any test or in CI.** The real providers were not reached from the machine that built this step either. The first real run is yours.

## 9. Check your understanding
1. You run `ekb demo gateway` twice in a row. Why does scene 3 send a request both times, while scene 9 never does? What makes the pipeline (from S8) different?
2. `context_header` runs Google → Groq → Mistral → OpenRouter. At 10:00 Google's daily quota is used up and Groq answers "429" for a chunk; Mistral then answers. At 10:02 the pipeline sends exactly the same call again. What does the gateway do at 10:02, and which records does the cost log hold for the two calls?
3. Why is the prompt version part of the cache key, and why are the prompt and the reply *not* in the cost log?

<details>
<summary>Answers</summary>

1. The demo opens a fresh cache file for every run, so its scene 3 is always a miss. That keeps the story the same each time, and it costs one request per run. Scene 9 opens a *new* gateway on the *same* file inside the run, so it finds scene 3's reply. The pipeline uses the configured cache, `.cache/ekb/model-cache.sqlite`, which survives runs, so an unchanged chunk's header is a hit tomorrow too (D16 #1).
2. At 10:02 the gateway first asks the cache under every candidate's key. Mistral's reply from 10:00 is there under Mistral's key, so the call is a **hit**: no request to anyone, $0, and Google and Groq are never considered. The log holds three records. At 10:00 there are Groq `rate_limited` (it counts against Groq's quota and starts its 10-minute rest) and Mistral `ok`. At 10:02 there is Mistral `hit`. Google's skip at 10:00 left no record, because no request was made. A *different* chunk at 10:02 would skip Google (quota used up until 08:00 UTC, midnight Pacific) and Groq (rate-limited 120 s ago), and go to Mistral.
3. Rewording a prompt already changes the key, because the text is in it. The version is for changes the text does not show: the step reads the reply differently, or you want fresh answers on purpose. Bumping it gives every call a new key at once (demo scene 5), and it labels each call in the cost log with the prompt it used. The text stays out of the log because the log is shown in the inspector and kept for quotas and the budget. With text in it, it would hold copies of documents: restricted ones like `HR-CMP-01`, and deleted ones, which must never be returned. The key, a hash, is enough to tell that two calls were the same; quotas and spend need only numbers.
</details>
