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
- ✅ AI-SDLC tools chosen: OpenCode with ChatGPT Plus, Copilot (GitHub Education), Claude; `AGENTS.md`; cross-review (D17)

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

### 4.2 Deliverable 2 — POC

**Use case (proposed):** HR assistant. Employees ask about policies, procedures and benefits in natural language; answers are cited, respect the asker's permissions, and reflect edits and deletions in the source without manual steps.

**Simplifying assumptions to state:** mocked connectors, synthetic HR documents, one region, small data volume, local Docker Compose only, optional Bedrock for models.

| # | Task | Status | Est. time |
|---|---|---|---|
| 1 | Create the GitHub repository and the skeleton (`src/`, `tests/`, `compose.yaml`, `README.md`) | 🟡 Skeleton, Docker, CI/CD and layer rules done (`docs/repo-structure.md`); GitHub repository not created yet | 15 min |
| 2 | Write `AGENTS.md` and `opencode.json` (rules for the AI coding tools, model per task) | 🟡 `AGENTS.md` done; `opencode.json` left | 15 min |
| 3 | Docker Compose core: Kafka, OpenSearch, DynamoDB Local, SeaweedFS (optional), Ollama | 🟡 Kafka, OpenSearch, DynamoDB Local, Ollama in `compose.yaml`; not yet run; SeaweedFS left | 30 min |
| 4 | Mock HR docs source with ACLs and webhooks + synthetic HR documents (several languages, one restricted doc) | ⬜ | 30 min |
| 5 | Connector → T1 normalized change event → Kafka | ⬜ | 30 min |
| 6 | Worker: T2 diff, T3 parse, T4 chunk + context, T7 embed, T8 permissions → OpenSearch (T5, T6 simplified) | ⬜ | 60 min |
| 7 | Model-call cache keyed by content hash, per-task model config, cost logging | ⬜ | 20 min |
| 8 | Knowledge API exposed as MCP tools: Q2 permission pre-filter, Q3 hybrid search, Q4 re-check and cite | ⬜ | 45 min |
| 9 | Demo of self-maintenance: add, edit and delete a document and show the answer change | ⬜ | 15 min |
| 10 | Tests: a small evaluation set (P1 options 4.4, 4.5, 4.13, 4.14 from `option-evaluation.md`), a permission-leak test, a deletion test | ⬜ | 30 min |
| 11 | `README.md`: setup, run, demo script, stated assumptions | ⬜ | 20 min |
| 12 | AI-SDLC write-up: how AI was used in design, implementation, testing and review | ⬜ | 15 min |
| 13 | Push to GitHub and check a clean setup from scratch | ⬜ | 15 min |

**POC total:** about 5 h 30 min of work if nothing goes wrong. If time runs short, drop in this order: SeaweedFS → Keycloak (use static test users) → T5 / T6 → observability.

---

## 5. Open points and risks

- **Scope note vs resizing:** the reviewers asked for a global-scale design; we sized for ShopFully. Mitigation: state it as an explicit assumption and document the scale-up path (§4.1 task 5).
- **Two design versions exist** (the PDF in `output/` and the files in `docs/`). Pick one before submitting.
- **Time:** the design work is already large; the POC must stay within the core list above.
- **Model cost:** stays within $10–25 only if the cache and batch settings are in place before the first full ingestion.
- Further risks and open questions: `decisions.md` Part C.

---

## 6. Next step

Start the POC (§4.2 tasks 1–3): repository skeleton, `AGENTS.md` / `opencode.json`, and the Docker Compose core. In parallel, decide which design version is the official one (§4.1 task 1).
