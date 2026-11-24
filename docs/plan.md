# Plan — Enterprise knowledge base for AI agents

> ShopFully & Flipp AI Engineer take-home. Last updated: 23 November 2026.
> Companion files: `architecture.md` (diagrams), `decisions.md` (choices and trade-offs), `option-evaluation.md` (how each choice is tested).

**Status key:** ✅ done · 🟡 in progress / needs an update · ⬜ not started

---

## 1. What we have to deliver

| Deliverable | What the assignment asks | Status |
|---|---|---|
| **D1 · Design document** | One document covering architecture, data connection, knowledge representation, agent access, self-maintenance, technology choices, trade-offs, quality evaluation, a clear diagram and explicit assumptions | 🟡 Content exists across three files; not yet assembled into one document |
| **D2 · POC** | A working, lightweight POC on one use case, built with AI coding tools, with readable code, setup instructions and a short AI-SDLC description, shared on GitHub | ⬜ Not started |

**Time budget:** 4–6 hours in total (assignment's expected effort).
**Model and embedding budget for the POC:** $10–25 (optimized profile in `decisions.md` D16).

---

## 2. The architecture in one paragraph

Department sources (docs tools, record systems, databases, identity provider) are read by **connectors** that turn every change into one normalized change event (**T1**). Events go through an **event backbone** (EventBridge + SQS FIFO on AWS, Kafka locally) and the originals land in a **versioned raw store** (S3 / SeaweedFS). **Workers** diff each event against a document registry (**T2**), then parse (**T3**), chunk with a context header (**T4**), classify (**T5**), extract and resolve entities (**T6**), embed (**T7**) and resolve permissions (**T8**). Results go into **knowledge stores**: a hybrid search index (OpenSearch), an entity store (DynamoDB, Neptune in phase 2), a metrics store (Athena / DuckDB), Cedar policies and a permission store. **Agents** reach the knowledge only through an **access layer** (AgentCore Gateway / auth middleware + Knowledge API over MCP), which plans the query (**Q1**), pre-filters by permission (**Q2**), runs hybrid retrieval with reranking (**Q3**) and re-checks, redacts and cites before answering (**Q4**). Docker Compose shares the worker logic and Knowledge API contract. Local Kafka supports replay, while production rebuilds from versioned S3 originals.

Full diagrams: `architecture.md`. Reasons for each choice: `decisions.md`.

---

## 3. What is done

### Research and design
- ✅ Research sources identified (AWS docs, open-source projects, ShopFully / Flipp / ShopX company context)
- ✅ Service comparison under the constraint "AWS or open source only"; excluded services listed with reasons (Kendra, Q Business, Bedrock Agents Classic in maintenance; Onyx not used; LocalStack needs a token; MinIO archived)
- ✅ Decision: build our own pipeline rather than adopting Onyx (`decisions.md` D1, D12)
- ✅ Company sizing revised to ShopFully / Flipp (~100–500 employees): SQS instead of MSK, DynamoDB entity store before Neptune, EU + Canada deployments, lean AWS profile (`decisions.md` §0, D3–D6, D18)
- ✅ Model routing by task: Haiku / Sonnet / Opus 5.5, provider switch `PROVIDER=ollama|bedrock` (D15)
- ✅ 14 cost optimizations → $10–25 POC budget (D16)
- ✅ AI-SDLC tools chosen: Codex (ChatGPT Plus) and Claude Code, cross-reviewing each other; Claude for design; Copilot optional; `AGENTS.md`; shared skills (D17)

### Documents in `docs/`
- ✅ `decisions.md` — decision log D1–D18, risks, open questions, dated sources
- ✅ `option-evaluation.md` — test set (~100 questions, 7 languages), metrics, 19 option cards with priorities, experiment budget, results template
- ✅ `architecture.md` — Mermaid diagrams of production on AWS and the local Docker Compose version, plus a transformations table
- ✅ `arch_aws.png`, `arch_local.png` — earlier diagram exports. Use the current Mermaid diagrams in `architecture.md` for the publication and access-check flows

### Diagrams
- ✅ Whiteboard artifact with production and local versions (https://claude.ai/artifact/YazT11CmDkfHyaCt1ajphY)
- 🟡 The whiteboard still shows the earlier large-enterprise sizing (MSK, Neptune from day one, three regions); `architecture.md` is the current version

---

## 4. What is left

### 4.1 Deliverable 1 — design document

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Reconcile `output/pdf/enterprise-knowledge-design.pdf` with the current decisions | ⬜ | That PDF uses ECS, Aurora and a "10 M passages / 100 QPS" sizing that differs from `decisions.md`; keep one version |
| 2 | Assemble one design document with the eight required sections | ⬜ | Sources: `architecture.md` → Architecture; D3 → Data connection; D7–D8 → Knowledge representation; D8–D9 → Agent access; T2 + deletes → Self-maintenance; D1–D18 → Technology choices; trade-offs + Part C → Trade-offs and risks; `option-evaluation.md` → Quality evaluation |
| 3 | Add a monitoring and incident section to Quality evaluation | ⬜ | What is watched over time (freshness lag, deletion propagation, permission leaks, retrieval quality drift, cost), thresholds, and the action for each alert |
| 4 | Make the assumptions section explicit and numbered | 🟡 | Started in `decisions.md` §0; the reviewers use it as the starting point for the discussion |
| 5 | Address the scope note | ⬜ | The assignment says to describe a full production-grade system for a global company and not simplify it to match the POC. State the ShopFully sizing as the assumption **and** show the scale-up path (MSK, Neptune, S3 Vectors, more regions) with the triggers for each |
| 6 | Update the whiteboard to the resized design (optional) | ⬜ | Or use the `architecture.md` diagrams as the official ones |
| 7 | Final read-through: every choice has a reason and a rejected alternative; known vs assumed is clearly separated | ⬜ | This is what the reviewers weigh most |

### 4.2 Deliverable 2 — POC, built step by step

**Use case:** HR assistant. Employees ask about policies, procedures and benefits in natural language; answers are cited, respect the asker's permissions, and reflect edits and deletions in the source without manual steps.

**Simplifying assumptions to state:** mocked connectors for four source types (docs tool, record API, database, identity provider), synthetic HR data, one region, small data volume, local Docker Compose only. Models come from free API tiers (Google AI Studio, Groq, Mistral, OpenRouter) through the model gateway, in development and in the POC's production alike; the synthetic data is assumed fine to send to them (decided 24 November 2026, D15). The production design keeps Bedrock.

#### How each step is built

The POC follows the architecture in order (sources → T1 → change log → T2 … T8 → stores → Q1 … Q4 → agent), **one step at a time**, so every piece is understood before the next one depends on it.

Each step goes through the same four stages:

1. **Build (Claude Code, `/plan-task S<n>`):** plan first, then code and tests. Use the simplest option first (in-memory, rules, no model); real infrastructure and models come in later steps as extra options of the same port.
2. **Understand:** each step ends with:
   - a walkthrough in `docs/walkthrough/` covering what was built, how the data flows, the files involved, and what the tests prove
   - a demo command you run yourself (`uv run ekb demo <step>`)
   - from S3 on, a small read-only panel in the inspector UI showing this step's output (see S2b)
   - three "check your understanding" questions
3. **Review (Codex, `$cross-review`):** review, fix, re-check, and confirm that the walkthrough matches the code.
4. **Push:** after your confirmation. A step is done only when you can answer its three questions.

A step is never started before the previous one is pushed. Exception, decided on 24 November 2026: S2, S2a and S2b are built together and reviewed and pushed as one batch.

**Tests are the proof that a step works; the UI is for seeing and understanding it.**

#### Steps

| Step | Layer / part | What gets built | What you will understand | Demo you run | Est. | Status |
|---|---|---|---|---|---|---|
| S0 | Foundations | Contracts (`ChangeEvent`, `Chunk`), option registry, config profiles, CLI, CI/CD, Docker, layer rules | How the code is organized and how options are swapped | `uv run ekb options` | — | ✅ |
| S1 | 1 · Sources | Mock HR docs source with ACLs + ~12 synthetic documents (EN, IT, ES; one restricted; one to delete later) | What the source systems look like and what "a change" is | `uv run ekb demo s1`: list documents, edit one, see its version change | 30 min | ✅ |
| S1b | 1 · Sources | Mock HR record API (stands in for Workday): employee and team records over REST with an `updated_since` filter that **never reports deletes**; in-memory | Pull-based sources, and why a deleted record can only be found by a full listing | `ekb demo s1b`: change a record and poll with `updated_since`; delete one and see the delta stay silent | 25 min | ✅ |
| S1c | 1 · Sources | Mock identity provider (stands in for Okta / Entra ID): the users and groups of `directory.yaml` served SCIM-style, with a change feed for joiners, leavers and group membership changes; in-memory | Why a group change is a permission change for every document that group can read | `ekb demo s1c`: remove alice from a group → one membership event, no document changed | 20 min | ✅ |
| S1d | 1 · Sources | Mock HR database (stands in for Postgres + Debezium): SQLite tables (headcount per office, leave balances) with a CDC-style change log written by triggers, deletes included | Change data capture, and why table rows are queried with SQL instead of embedded (D7) | `ekb demo s1d`: insert, update and delete rows → three change-log entries in commit order | 25 min | ✅ |
| S2 | 2 · Connect + **T1** | Connector (webhook-style and full-listing) → normalized `ChangeEvent` | Why every source is reduced to one event shape | `ekb demo t1`: add, edit, delete and revoke access in the source → print the 4 events | 30 min | ✅ |
| S2a | 2 · Connect + **T1** | Connectors for the other three sources, each an option of the connector port: delta polling + full listing (HR API), CDC reader (database), identity sync (identity provider) → the same `ChangeEvent` | That one event shape covers push, pull, CDC and identity sources, so nothing downstream depends on the source type | `ekb demo t1 --all`: one change in each of the four sources → four events of the same shape, side by side | 45 min | ✅ |
| S2b | 8 · Inspector UI foundation | Read-only inspector API (FastAPI, **local profile only**) + React + TypeScript app (Vite): **Documents** screen and **Events** panel; TypeScript types generated from the API (OpenAPI → `openapi-typescript`), CI fails if they are stale; UI lint, type check and build in CI | How the data looks at each stage, visually; how front end and back end share one contract | `uv run ekb inspector` + `npm run dev` in `ui/` | 90 min | ✅ |
| S3 | 3 · Change log | Event bus, document registry, raw store: **in-memory** options | Ordering per document, replay, why originals are stored | `ekb demo s3`: publish, consume, replay · UI: change log panel | 40 min | ✅ |
| S4 | 4 · **T2** Diff | Skip unchanged, ACL fast lane, delete → purge | How the system avoids re-processing and handles deletes | `ekb demo t2`: same doc twice → second skipped; ACL change → fast lane · UI: T2 decision panel | 30 min | ✅ |
| S5 | 4 · **T3** Parse | Plain-text and Markdown parser (Docling as a later option) | From file to clean structured text | `ekb demo t3` on one document · UI: parsed text panel | 25 min | ✅ |
| S6 | 4 · **T4** Chunk | Structure-aware chunking; context header off for now | Why chunk boundaries and headers matter for retrieval | `ekb demo t4`: show chunks of one policy · UI: chunks panel | 30 min | ✅ |
| S7 | Platform · Model gateway | Free-tier providers through LangChain (Google AI Studio, Groq, Mistral, OpenRouter; fake offline; Ollama optional), per-task failover on quotas, content-hash cache, cost log, spend cap | How every model call is cached, routed and costed | `ekb demo gateway`: same call twice → second from cache, cost logged · UI: model calls and cost panel | 40 min | ✅ |
| S8 | 4 · **T4** header + **T5** Classify | Context header via the gateway; classification by rules (+ model option) | What metadata each chunk carries and why | `ekb demo t5` · UI: labels on chunks | 40 min | ⬜ |
| S9 | 4 · **T6** Entities | Exact match against the people and teams from the HR record API (S1b) (model cascade later) | How documents get linked to people and teams | `ekb demo t6` · UI: entities panel | 30 min | ⬜ |
| S10 | 4 · **T7** Embed | Ollama bge-m3 option next to the fake one | What a vector is and why the model ID is stored | `ekb demo t7`: similar vs different sentences · UI: vector info on chunks | 30 min | ⬜ |
| S11 | 4 · **T8** + 5 · Search store | Permission resolution (page ACLs + group membership from the identity source, S1c), publication gate, **in-memory** search store | Why nothing is searchable before permissions are set | `ekb demo t8`: chunk invisible until T8 runs · UI: permissions and publication status | 40 min | ⬜ |
| S12 | 4 · Worker pipeline | Wire T2 → T8 on the bus, end to end | The full write path | `ekb demo ingest`: source → searchable chunks · UI: full pipeline trace per document | 40 min | ⬜ |
| S13 | 6 · **Q2 + Q3** | Permission pre-filter + hybrid search (BM25 + vectors, RRF) | How retrieval respects who is asking | `ekb demo search --user alice` vs `--user bob` · UI: **Search as user** screen, two users side by side | 70 min | ⬜ |
| S14 | 6 · **Q4 + Q1** | Re-check against the source, redaction, citations; simple query planning | Why a second permission check exists | `ekb demo answer` with citations · UI: citations and redactions in search results | 40 min | ⬜ |
| S14b | 5 · Metrics store + 6 · Q1 | *Optional.* Structured path: database change events → in-memory metrics store (DuckDB option later); Q1 sends counting questions ("how many people work in Madrid?") to SQL instead of search (D7) | Why structured data takes a different path from documents | `ekb demo answer "how many people work in Madrid?"` → answer from SQL, cited to the table | 45 min | ⬜ |
| S15 | 6 · MCP + 7 · Agent | Knowledge API as MCP tools; static test users; small HR assistant | How an agent uses the knowledge base safely | Ask the HR assistant from Claude Code / MCP Inspector | 40 min | ⬜ |
| S16 | Self-maintenance | Scripted scenario across the sources: add, edit, delete a page; delete an HR record (found by the full listing); revoke on the page **and** by removing a user from a group → answers change; gate tests | The "maintains itself" requirement, proven | `ekb demo lifecycle` · UI: **Lifecycle** screen (add, edit, delete, revoke → see the effect) | 70 min | ⬜ |
| S17 | Real infrastructure | Kafka, OpenSearch and DynamoDB options; `docker compose --profile app up`. Optional: Postgres + Debezium and Keycloak as extra options behind the S1d and S1c connectors | That swapping infrastructure doesn't change the logic | Same demos against Docker | 45 min | ⬜ |
| S18 | 8 · Evaluation | Golden question set, runner, P1 experiments 4.4, 4.5, 4.13, 4.14 | How each choice is measured | `uv run ekb-eval run …` | 60 min | ⬜ |
| S19 | Finish | README (setup, demo, assumptions), AI-SDLC write-up, clean setup check | — | Fresh clone → follow README | 30 min | ⬜ |

**Estimate:** about 14–16 hours of build time, including about 3–4 hours of UI and about 2 hours for the extra source types (S1b–S1d, S2a; the optional S14b adds 45 minutes), plus the time you spend reading walkthroughs. That is more than the assignment's 4–6 hours because the steps are built to be understood, not just delivered.

**Minimum path if time runs short:** S1 → S6 (including S1b–S1d and S2a), S10 → S16 (in-memory stores, no models except embeddings). Skip S7–S9, S14b and S17 and state them as planned. If more must go, drop S1d and the database connector next: S1b and S1c stay, because they feed the deletion and revocation gates. That still demonstrates the write path, permissions, self-maintenance and the MCP access layer. For the UI, the per-step panels are skipped first; S2b and the **Search as user** (S13) and **Lifecycle** (S16) screens are kept, because they show the two hardest requirements in the interview.

---

## 5. Open points and risks

- **Scope note vs resizing:** the reviewers asked for a global-scale design; we sized for ShopFully. Mitigation: state it as an explicit assumption and document the scale-up path (§4.1 task 5).
- **Two design versions exist** (the PDF in `output/` and the files in `docs/`). Pick one before submitting.
- **Time:** the design work is already large; the POC must stay within the core list above.
- **Model cost:** stays within $10–25 only if the cache and batch settings are in place before the first full ingestion.
- Further risks and open questions: `decisions.md` Part C.

---

## 6. Next step

S7 is complete and reviewed (✅). See the [model gateway walkthrough](walkthrough/S07-model-gateway.md). Settled in S7's plan on 24 November 2026 (D15):
- Free API tiers, not local models, for development and the POC's production: Google AI Studio, Groq, Mistral and OpenRouter, tried in order per task, with failover when a quota is used up. Ollama is an optional provider; the production design keeps Bedrock.
- OpenRouter and the other free tiers are recorded in D15 as an exception to the "AWS or open source only" constraint, for the POC only.
- LangChain's chat models make the calls; the gateway's own code keeps routing, the cache, quotas, the spend cap and the cost log.

The next step is **S8** (context header + T5 classify) with `/plan-task S8`, the gateway's first real caller. Its plan must also wire `forget(document_key)` to T2's purge.

Idea for later (T4, card 4.2): a chunk size that adapts to the text instead of one limit for every document.

In parallel, decide which design version is the official one (§4.1 task 1).
