# Enterprise Knowledge Base — Decision Log

> Decisions and trade-offs taken while designing the system (Deliverable 1) and the POC (Deliverable 2) of the ShopFully & Flipp AI Engineer take-home.
> Started: 23 November 2026. Revised the same day to size everything for ShopFully / Flipp (~100–500 employees) instead of a 20–50k-employee enterprise.
> Prices and service status were checked on that date and change often — recheck before the discussion session.

Each decision lists: **what we decided**, **why**, **what we rejected**, **the trade-off we accept**, and **when to revisit**.

Choices that can be measured are tested option by option in `option-evaluation.md`; until a result exists, the choice here is the **starting choice**, not a final verdict.

Companion artifact: the architecture whiteboard (production on AWS + the same design running locally on Docker Compose).

---

## 0. Company context and assumptions (starting point for the discussion)

**What public sources say (November 2026):**
- **ShopFully** (HQ Milan, teams in Bologna and elsewhere) runs local-shopping marketplaces (DoveConviene, PromoQui, VolantinoFacile, Tiendeo) and drive-to-store / retail-media products. Its LinkedIn page lists 201–500 employees ("over 450 people") across 25 countries in Europe, Australia and Latin America.
- **Flipp** (HQ Toronto, also Vancouver) runs the Flipp digital-flyer app in North America. Its LinkedIn page also lists 201–500 employees.
- ShopFully and MEDIA Central merged in 2023, then merged with Flipp (closed November 2024). On 2 November 2026 the group launched as **ShopX** (Shopfully, Flipp, MEDIA CENTRAL): 27 markets, 400 million shoppers, a team of more than 1,000. The brands keep operating separately.

**Assumptions:**

| Topic | Assumption |
|---|---|
| Company size | **~100–500 employees** in scope (one operating company, e.g. ShopFully or Flipp). The design must grow to the ~1,000+ ShopX group without a redesign |
| Departments | Sales and account management (retailers, brands), Client Experience / Operations (flyer and content processing), Product, Engineering, Data / BI, Finance, People |
| Sources | ~20–40 sources. After the mergers there are likely two or more tools for the same job (two wikis, two CRMs, two identity providers) — exactly the heterogeneity the brief describes |
| Volume | ~100k–1M documents (~1–10M chunks); ~10–50k changes per day |
| Query load | Up to ~5–20 agent queries per second at peak |
| Freshness targets | Content changes visible in < 15 min; copied permissions updated in < 5 min. Every document response still requires a current access check (D8) |
| Residency | EU (GDPR) and Canada (PIPEDA), plus Australia and Latin America → separate deployments at least for the EU and Canada |
| Platform constraint | **Only AWS services or open-source software** |
| Effort | Assignment budget is 4–6 hours total, so the POC must be small |

**What changes compared with a 20–50k-employee design:**
- Fixed monthly costs matter more than throughput, so production starts on **serverless, scale-to-zero services** (D4, D5).
- A managed RAG product's lower operating cost weighs more, so the **bake-off in D1 matters more**.
- The knowledge graph is **phased in** rather than built on day one (D6).
- Bulk LLM enrichment drops from a six-figure risk to **about $500 one-off** (Part C).
- Merging **two or more identity providers** becomes a core requirement (D3, D8).

---

## Part A — Production architecture

### D1. Build our own core instead of a managed RAG product

- **Decision:** Build the knowledge platform from AWS building blocks. We own the control plane (change log, knowledge model, permissions, evaluation) and rent the commodity parts (parsing, embeddings, storage, agent gateway).
- **Why:** The main managed option, **Amazon Bedrock Managed Knowledge Base** (GA June 2026), covers only document connectors (S3, SharePoint, Confluence, Google Drive, OneDrive, web crawler; ServiceNow and Confluence Data Center added in September 2026). It has no record systems, no knowledge graph and only one parsing strategy; it uses AWS-managed internal storage only; it is in 7 commercial regions; and AWS itself says its ACL-aware filtering is **not a security boundary** and group memberships are only as fresh as the last sync.
- **Rejected:** Bedrock Managed Knowledge Base as the core; Bedrock Knowledge Bases (customer-managed) as the core.
- **Trade-off:** More engineering and operations in exchange for control over the knowledge model, permissions and freshness. At ShopFully / Flipp size the managed option's lower operating cost is a real argument, but its gaps (no record systems or structured data, permissions not a security boundary) remain.
- **Revisit:** Run a bake-off. For the sources it covers, compare Bedrock Managed Knowledge Base with our index on the same golden questions. If it wins, put it behind our access layer as one more retriever.

### D2. Exclude AWS services in maintenance mode

- **Decision:** Do not use **Amazon Kendra**, **Amazon Q Business** or **Bedrock Agents Classic**.
- **Why:** All three stopped accepting new customers in July 2026 (AWS recommends Bedrock Managed Knowledge Base, Amazon Quick and Bedrock AgentCore respectively).
- **Trade-off:** We lose Kendra's 30+ ready-made connectors, so we build or assemble connectors ourselves.

### D3. Connectors: a mix of tools behind one change-event contract

- **Decision:** Every connector emits the same normalized event (**T1**): source, ID, operation (upsert / delete / ACL change), version, timestamp, ACL, pointer to the original in S3.
  - Document tools → our own connectors on Fargate / Lambda (webhooks through EventBridge, delta APIs, ACL crawl)
  - SaaS record systems → **AWS Glue zero-ETL** (Salesforce, ServiceNow, SAP, Zendesk → S3 Tables)
  - Operational databases → **AWS DMS change data capture** (log-based, deletes included); Debezium on MSK Connect once Kafka exists (see D4)
  - Identity providers → identity sync (SCIM user, group and role changes become events). After the mergers there may be two or more identity providers, so the sync maps each person to **one canonical person ID**
  - **Nightly full listing per source**, diffed against the registry, to catch deletes from sources without CDC or webhooks
- **Why:** No single tool captures both content and permissions for every source. A common contract keeps everything downstream source-agnostic.
- **Rejected:** Airbyte OSS (doesn't carry permissions; misses records that simply disappear from non-CDC sources); Onyx connectors (permission sync needs the paid Enterprise Edition); Amazon AppFlow (overlaps with Glue zero-ETL); Bedrock Managed Knowledge Base connectors (tied to its own index).
- **Trade-off:** We own and maintain the custom connectors, which are the most expensive and least differentiating part of the system.

### D4. Change log: serverless event backbone now, Kafka when scale demands it *(revised for company size)*

- **Decision:** Production starts with **EventBridge** for intake and **SQS FIFO queues grouped by document ID** for processing (per-document ordering). The **versioned originals in S3 plus the document registry** are the replay source: rebuilding an index means re-reading them. All code talks to an **event-bus interface**, with two implementations: SQS on AWS and **Apache Kafka in the local POC**, where it is free and demonstrates tombstones and replay.
- **Why:** At ~10–50k changes per day, MSK Serverless would cost about $560/month just for existing (see D18) — most of the platform's fixed cost — for throughput we don't need. Replaying from S3 is quick at 1–10M chunks.
- **Rejected for now:** Amazon MSK with compacted topics (the original choice; still the scale-out path); Kinesis (no compaction; replay limited by retention).
- **Trade-off:** Two implementations of the event-bus interface; no log compaction in production at first; replay is a scan of S3 rather than a log read.
- **Revisit:** Move to MSK at more than ~1M changes per day, when several independent consumers need the same stream, or for a ShopX-wide rollout.

### D5. Search index: OpenSearch, sized small *(revised for company size)*

- **Decision:** **Amazon OpenSearch** for hybrid search: BM25 keyword search + vectors + metadata filters (user and group IDs, entity IDs, freshness). Start with the **new OpenSearch Serverless generation** (no minimum capacity; scales to zero after 10 minutes without traffic) or one small managed domain. Add **S3 Vectors** as a cold tier only if vectors grow into the tens of millions.
- **Why:** Exact terms (retailer names, campaign codes, SKUs) need keyword search; meaning needs vectors; permissions need filters — all in one engine, the same one that runs in the local POC. At 1–10M chunks, fixed cost matters more than peak capacity.
- **Rejected:** OpenSearch Serverless classic (minimum around $350/month); Aurora PostgreSQL + pgvector (viable at this scale, but keyword ranking is weaker and merging it with vector results is manual — kept as the fallback); S3 Vectors alone (no keyword search); Qdrant / Milvus / Vespa on EKS (more to operate).
- **Trade-off:** The new Serverless generation launched in 2026 and is less proven than managed domains.

### D6. Knowledge graph: phased in, ending on Neptune Database *(revised for company size)*

- **Decision:**
  - **Phase 1:** an entity store (people, teams, retailers, brands, campaigns, products, policies, documents) in DynamoDB, filled by entity resolution; entity IDs are written onto each chunk in the search index. This already answers "everything about retailer X".
  - **Phase 2:** **Amazon Neptune Database** (openCypher) for relations with valid-from / valid-to, once agents such as the roadmap agent need multi-hop questions across departments. The graph is queried directly only for explicit relationship questions.
- **Why:** A cross-department model needs a schema we control. Dropbox (Dash) found runtime graph queries too slow and folds graph information into the index instead.
- **Rejected:** Bedrock Knowledge Bases GraphRAG on Neptune Analytics (S3 is the only data source, the graph build can't be customized, no autoscaling); Neo4j (clustering needs the paid Enterprise edition — fails the open-source rule); Apache AGE (not mature enough).
- **Trade-off:** We own entity resolution, which is hard and error-prone. Phasing avoids paying for a graph database before there are questions that need it, at the cost of a migration later.

### D7. Structured data: query it with SQL, don't embed it

- **Decision:** Finance and Sales numbers stay as tables (S3 Tables), queried with **Athena / Redshift**, with **Lake Formation** row- and column-level security, exposed to agents as a governed SQL tool.
- **Why:** Embedding table rows gives wrong answers for numbers and aggregates.
- **Trade-off:** Agents need a reliable natural-language-to-SQL step (a semantic layer helps).

### D8. Authorization at retrieval and release

- **Decision:**
  1. Source permissions are copied into the index as user and group IDs and used as a **pre-filter**.
  2. Company and agent policies (e.g. "the roadmap agent may read Sales data but never HR data", sensitivity labels) live in **Amazon Verified Permissions (Cedar)**; **AgentCore Policy** enforces Cedar rules at tool level.
  3. Before releasing document text, an answer or a citation, the API checks every contributing source document for the acting user and agent. The HR mock reads its current permissions directly. In production, use a source read under the user’s identity or an authoritative access check that accounts for current group membership. A denial, deletion, timeout or unknown result excludes that document. Regenerate an affected answer from the remaining authorized evidence, or withhold it.
- **Why:** Metadata filtering alone isn't enough when users belong to hundreds of groups (AWS's own security guidance says so). Copied permissions are always slightly stale.
- **Rejected for now:** OpenFGA / SpiceDB on EKS. Kept in reserve if we need relationship-based sharing or efficient "list everything this user can see" queries, which policy engines like Cedar handle poorly at scale.
- **Trade-off:** A source with a live user-scoped read can enforce revocation on the next request. A connector with only periodic permission snapshots can measure sync lag but cannot confirm current access. Withhold its documents until an authoritative check is available. This can reduce coverage during source outages.

### D9. Agent access: our Knowledge API as MCP tools behind AgentCore Gateway

- **Decision:** A Knowledge API (Fargate) exposes MCP tools (`search`, `get_document`, `get_entity`, `query_metrics`, `changes_since`) through **Bedrock AgentCore Gateway**. **AgentCore Identity** uses on-behalf-of token exchange, so agents act as the signed-in user. Bedrock Guardrails redacts PII in outputs.
- **Per-request steps:** Q1 plan the query → Q2 pre-filter with user and group IDs + Cedar policies → Q3 hybrid retrieval, graph hops, rerank → Q4 re-check the top results, redact PII, add citations.
- **Why:** One MCP endpoint for all agents, default-deny Cedar policies, and no super-user service account.
- **Rejected:** A self-hosted MCP server behind API Gateway (we would build OAuth and policy enforcement ourselves); Bedrock Agents Classic (maintenance).

### D10. Parsing and embeddings

- **Decision:** **Docling** (open source) by default; **Bedrock Data Automation** only for scanned or visual documents (priced per page). **Cohere Embed v4** on Bedrock for embeddings (multilingual and multimodal; 256–1536 dimensions; int8 and binary formats). The model ID is stored with every vector.
- **Why:** Pay for expensive parsing only where it is needed; storing the model ID makes re-embedding a planned batch job.
- **Rejected:** A foundation model as parser for everything (cost); Textract alone (no chart or image understanding); self-hosted embeddings on SageMaker (only if residency or cost demands it).

### D11. Evaluation and monitoring

- **Decision:** OpenTelemetry traces → CloudWatch / AgentCore Observability; **AgentCore Evaluations** scores sampled live traffic; **Ragas** in CI on golden questions per department; custom **freshness metrics** (source-to-index lag, delete propagation, permission lag, reconciliation differences).
- **Why:** Off-the-shelf tools don't measure freshness, and freshness is a core requirement.
- **Rejected:** Langfuse self-hosted (good, but one more system to run).

---

## Part B — POC

### D12. Build our own POC rather than adopt Onyx

- **Decision:** Write a small POC ourselves. Cite Onyx as prior art and as a rejected "buy" option.
- **Why:** Onyx's permission sync (the core "authorized" requirement) is in the paid Enterprise Edition, which also breaks the open-source rule. The graders want our code and reasoning, not configuration of a large codebase. Onyx needs a base of 4 CPU cores and 16 GB of RAM, which makes it hard for reviewers to run.
- **Trade-off:** We don't get Onyx's 40+ ready-made connectors.

### D13. Local Docker Compose and production adapters

- **Decision:** The POC runs locally with `docker compose up`. Each AWS service gets a maintained local replacement:

| Production (AWS) | Local | Same code? |
|---|---|---|
| EventBridge + SQS (MSK at scale) | Apache Kafka (KRaft, 1 node), behind the same event-bus interface | Partly |
| S3 / S3 Tables | SeaweedFS (S3 API), Parquet | Partly |
| DynamoDB | DynamoDB Local | Yes |
| OpenSearch Service | OpenSearch (1 node) | Yes |
| Neptune | Neo4j Community, openCypher only | Partly |
| Athena / Redshift | DuckDB | Partly |
| Verified Permissions | Cedar library, same `.cedar` files | Yes |
| AgentCore Gateway | Auth middleware in the Knowledge API | Partly |
| Okta / Entra ID | Keycloak | Partly |
| Bedrock Guardrails | Presidio | No |
| CloudWatch | OpenTelemetry Collector + Jaeger, Prometheus + Grafana | Yes |
| Glue zero-ETL | Polling connector | No |

- **Why:** About 1 day to build vs 2–3 days directly on AWS; seconds per change instead of minutes per deploy; reviewers can run it; **$0 infrastructure cost**.
- **Rejected:** One AWS emulator for everything — LocalStack requires an account and auth token since March 2026, and MinIO's open-source project was archived in February 2026.
- **Trade-off:** Some behaviour can't be proven locally: scale and latency, IAM / Lake Formation / AgentCore Policy, Glue zero-ETL, Bedrock Data Automation, and Neptune-specific openCypher differences.
- **POC core (4–6 h budget):** mock sources, Kafka, workers, OpenSearch, DynamoDB Local, Knowledge API. Neo4j, DuckDB, Presidio, Jaeger and Keycloak are optional add-ons.

### D14. OpenSearch locally instead of Postgres + pgvector

- **Decision:** Use OpenSearch in the POC too.
- **Why:** The same queries, mappings and permission filters as production, so retrieval behaviour carries over.
- **Trade-off:** Heavier (~1–2 GB RAM) than Postgres.

### D15. Models: one model per task behind a gateway; free tiers in the POC, Bedrock in production *(revised 24 November 2026, S7)*

- **Decision, production (unchanged):** one model per task on Bedrock, the table below. Architecture Part A stays as designed.
- **Decision, POC (S7):** every model call goes through the model gateway (`ekb.platform.gateway`). Each task has an ordered list of candidates on free API tiers: Google AI Studio (Gemini Flash-Lite, Gemini Flash), Groq (gpt-oss 20B / 120B), Mistral (Small / Large) and OpenRouter (`openrouter/free`). The gateway skips a candidate with no key, past its free quota or recently rate-limited, so a used-up quota moves the call to the next one. The POC uses the same free tiers for development and for its "production". `fake` serves the `ci` profile. Ollama stays an optional provider (`uv sync --extra ollama`), off by default, because the development machine has integrated graphics and the configured local model is not installed. A smaller local model completed the feasibility run below.
- **POC assumption:** the data is synthetic and assumed fine to send to these providers (plan §4.2). Real employee data would need a paid tier with a data processing agreement, no training on the data and EU processing, which is what Bedrock gives production. Google's own terms allow only paid services for applications available to users in the EEA.
- **Exception recorded:** OpenRouter, Google AI Studio, Groq and Mistral are outside the "AWS or open source only" constraint. Allowed in the POC only, under the assumption above.
- **How models are called: LangChain chat models, behind our own gateway.** LangChain gives one interface to every provider (a new provider is its `langchain-*` package and one line; a new model is configuration), structured output and standard token counts. What the gateway decides stays ours: per-task routing from config, the content-hash cache (with the prompt version, and `forget(document_key)` for deletes), quota counting and failover, the spend cap and the cost log. LangChain's own cache is not used: it keys on the prompt and model settings only, cannot drop a deleted document's replies and does not log hits. LangChain's retries are off; the gateway decides what follows a failure. Only `ekb.platform.gateway.options.langchain` imports LangChain (import-linter contract).
- **Default selection evidence.** The local hardware run and live gateway demo support keeping the free-tier routes as the POC default and Ollama optional. Cards 4.3 and 4.9 will compare answer quality. [S7 hardware feasibility result](../evaluation/results/2026-11-25-4.3-gateway-hardware.md)

| Task (gateway task) | Production model | POC candidates, in order | Why |
|---|---|---|---|
| T4 context header per chunk (`context_header`) | Claude Haiku 5.5 | Gemini Flash-Lite → gpt-oss-20b → Mistral Small → OpenRouter free | High volume, simple task |
| T5 classification (`classify`) | Haiku 5.5 + Presidio rules | same as T4 | Cheap; rules catch structured PII |
| T6 entity extraction and resolution (`entities`, `entities_hard`) | Haiku first, Sonnet 5.5 for hard cases | light chain, then Gemini Flash → gpt-oss-120b → Mistral Large → OpenRouter free | Mistakes pollute the graph |
| T7 embeddings | Cohere Embed v4 | decided in S10 (no local model) | Multilingual; same as production |
| Q1 query planning (`plan_query`) | Haiku 5.5 | light chain | Latency |
| Q3 reranking | Cohere Rerank 3.5 | decided in S13 | Large precision gain for little cost |
| HR assistant answers (`answer`) | Sonnet 5.5 | strong chain | Quality users see |
| Evaluation judge (`judge`) | Opus 5.5, or Sonnet if they agree | strong chain | Judge stronger than what it grades |

- **Prices (Nov 2026, per million tokens, input / output):** Haiku 5.5 $0.10 / $0.50 (Anthropic list price); Sonnet 5.5 $2 / $10 on Anthropic's API, possibly $3 / $15 on Bedrock (we budget with $3 / $15); Opus 5.5 $4 / $20; Cohere Embed v4 $0.12 input; Cohere Rerank 3.5 $2 per 1,000 queries; Bedrock batch inference −50%. The free tiers cost $0 and are limited by requests per minute and per day instead.
- **Rejected:**
  - **LiteLLM**, a gateway library covering much of the same ground: its PyPI releases were compromised twice in 2026 (1.82.7 and 1.82.8 in March, through the Trivy attack; 1.107.0 in October).
  - **Our own HTTP client per provider:** smaller, but every new provider would be new code to write and test.
  - **LangChain's `init_chat_model`:** it needs the `langchain` package, which pulls in LangGraph; the provider table in the LangChain option does the same in a few lines.
  - GitHub Models (retired 30 July 2026); GitHub Copilot SDK (an agent runtime, not a chat or embedding API; uses the Copilot quota); ChatGPT Plus (includes no API credits); Cerebras (its no-card free tier ended on 21 July 2026).
  - For production: OpenCode Go / Zen and third-party open-weight hosts such as DeepInfra or Together (cheap, but data leaves AWS).
- **Trade-off:** Free tiers have no SLA, small daily caps, and models that rotate out. Failover can answer the same kind of call with different models on different days; the cost log records which, and experiments (S18) pin one model with no fallback. Reviewers need free API keys to see real model output; everything else, and every test, runs offline on the fake model. In production, Sonnet 5.5 on Bedrock is served through the Global inference profile, so requests may be processed outside the EU. That is acceptable with synthetic data; production with EU employee data needs the EU profile. Haiku 5.5 does have an EU profile.
- **Revisit:** If bulk enrichment dominates production cost, re-evaluate open-weight models (gpt-oss on Bedrock, or third-party hosts if the AWS rule is relaxed).

### D16. Cost optimizations — target **$10–25** for the whole POC build

- **Decision:** Apply these without lowering accuracy:
  1. **Cache every model call by content hash** (model + prompt version + input) → re-ingestion during development is almost free. *Built in S7: a SQLite cache keyed on provider, model, model settings, prompt version and the full request.*
  2. **Prompt caching in T4:** the document is a cached prefix, then each chunk.
  3. **Bedrock batch inference** (−50%) for ingestion and evaluation.
  4. **One call per document** returning context headers + classification + entities as JSON, instead of one call per chunk.
  5. **A cascade for entities:** exact match against HR data → Haiku → Sonnet only on low confidence or conflicts.
  6. **Skip the model where code works:** no LLM for HR database records; strip boilerplate before enrichment.
  7. **Cap output length and require a JSON schema** (no retries, no long answers).
  8. **Embed once and reuse:** vectors stored with their content hash and model ID; rebuilding from Kafka reuses them. Use **int8, 1024-dimension** vectors.
  9. **Two levels of evaluation:** retrieval metrics (recall@k, MRR) with no LLM on every change; full LLM-judged runs only at milestones.
  10. **Cache answers** with the IDs and versions of every contributing document. On reuse, verify those versions and repeat D8 for the current caller. Discard the cached answer if any dependency is stale, unavailable or denied.
  11. **Rerank, then send only the top 5 chunks** to the answering model.
  12. **Calibrate the judge:** use Sonnet if it agrees with Opus on 20 questions; Opus only where they disagree.
  13. **Freeze the test questions**; develop on ~30 documents and run the full corpus only at milestones.
  14. **Guardrails on spend:** AWS Budgets alert at about $20; cost per step logged from token counts in traces. *S7: the gateway's cost log records every call's tokens and cost, and paid calls stop at `EKB_MAX_SPEND_USD`.*
- **Accuracy guard:** every optimization is an experiment. Run the frozen test set before and after, and keep the change only if scores stay within an agreed tolerance.
- **Estimate:** without optimizations about $30–100 for the build (~300 HR documents, ~1,500 chunks, 100 test questions, ~5 ingestions, ~10 evaluation runs); with them about **$10–25**.

### D17. AI-SDLC tooling

- **Decision:**

| Stage | Tool |
|---|---|
| Design, research, architecture document, cross-review | Claude (this chat) |
| Implementation tasks in the repository | Claude Code |
| Review, fixes and push | Codex (signed in with ChatGPT Plus) reviews every change, fixes what it finds, re-runs the checks and pushes after I confirm (different model family) |
| Inline completions (optional) | GitHub Copilot Student (unlimited completions; 200 AI credits a month for chat and agents; automatic model selection only) |

- **Practices:** one `AGENTS.md` at the repository root (architecture, constraints, conventions, test commands) read by every tool (Claude Code through a `CLAUDE.md` that imports it); repeatable workflows as shared skills in `.agents/skills/` (plan-task, new-option, cross-review); **fixed roles**: Claude Code writes; Codex reviews, fixes and pushes after I confirm, so an OpenAI model reviews Anthropic-written code; AI use recorded in commit trailers (`.gitmessage`) and in `docs/ai-sdlc.md`.
- **Rejected:** OpenCode. It would add a third agent tool to configure, while Codex and Claude Code already cover implementation and give cross-review across two model families.
- **Trade-off:** Two agent tools to coordinate; `AGENTS.md` and the shared skills keep them consistent. Because the reviewer also edits, its own fixes get no second AI review; they are kept small, listed in a `Review-fixes` commit trailer, re-checked by CI, and read by me before the push. Claude Code needs thin wrappers in `.claude/skills/` because it reads skills from its own folder.

### D18. Deploying the POC on AWS (optional bonus)

- **Decision:** Not part of the main deliverable. If we show it, use a **lean, scale-to-zero profile**: SQS instead of MSK, Lambda instead of Fargate (no VPC, no NAT gateway), OpenSearch free tier or the new Serverless generation, AgentCore Gateway with Lambda targets.
- **Why:** A production-shaped stack left on 24/7 costs about **$700/month**, about 80% of it MSK Serverless ($0.75 per cluster-hour); the NAT gateway adds about $33/month. A lean profile costs roughly **$0–30/month**; the full stack switched on only for 40 working hours costs about $40.
- **Trade-off:** The lean profile can't demonstrate Kafka-style replay and tombstone deletes. Since D4 now also starts production on SQS, the lean profile is close to the real production setup.

---

### D19. Inspector UI: React + TypeScript, built alongside the pipeline

- **Decision:** A local, read-only inspector: a FastAPI inspector API (`src/ekb/operate/inspector/`) and a React + TypeScript app built with Vite (`ui/`). The foundation comes in step S2b; after that, each step adds a panel showing its own output. Two larger screens are added in S13 (**Search as user**: the same question for two users, side by side) and S16 (**Lifecycle**: add, edit, delete or revoke, then see the effect).
- **Why:** It makes each step visible as it is built, and it shows the two hardest requirements, permission-aware retrieval and self-maintenance, in a way a terminal can't.
- **Safety:** The inspector shows data a normal user must never see, such as other users' documents and raw chunks. It is read-only, exists only in the local profile, binds to `127.0.0.1`, and a test checks it can't start under any other profile. Agents never use it; they go through the MCP access layer.
- **Contract:** TypeScript types are generated from the inspector API's OpenAPI schema (`openapi-typescript`). CI fails if they are out of date, so the front end and back end can't drift apart.
- **Rejected:**
  - A Streamlit page: about 1–2 hours instead of 3–4, but it shows less front-end engineering and keeps no typed contract.
  - Building the UI only at the end: cheaper, but it loses the visual check while each step is built.
- **Trade-off:** It adds about 3–4 hours spread across the steps, and early panels may need rework when later steps change a data shape. Tests, not the UI, remain the proof that a step works. If time runs short, the per-step panels are dropped first.

## Part C — Risks and open questions

| Risk | Mitigation |
|---|---|
| Permission staleness (copied ACLs lag the source) | Track permission sync lag per source. Apply D8 on every document response, including cache hits. |
| Deletes from sources without CDC or webhooks | Nightly full listing diffed against the registry |
| Re-embedding cost when the model changes | Replay from versioned S3 originals + model ID stored per vector |
| LLM enrichment cost (at ShopFully / Flipp size, ~5M chunks ≈ $500 one-off with Haiku 5.5 before discounts; at a 1B-chunk enterprise it would be ≈ $100k) | Enrich only changed chunks; batch; caching; consider open-weight models at larger scale |
| Merged companies with overlapping tools and identities | One canonical person ID across identity providers; one entity ID per retailer / brand across CRMs |
| Entity resolution errors pollute the graph | Cascade with deterministic matching first; graph quality checks |
| Regional availability (e.g. Bedrock Managed Knowledge Base in 7 regions, AgentCore Evaluations in 9) | Check every service against the residency map, especially for a Canada deployment |
| Fast-changing services and prices | Date every document; recheck before the interview |

**Open questions for the interviewers:** real volumes and freshness SLAs; which sources are in scope first; residency rules per country; how strict "authorized at the moment they act" is (seconds vs minutes); whether a read-only knowledge layer is enough or agents also need to write back.

---

## Sources (checked 23 November 2026)

- ShopX launch (Shopfully, Flipp, MEDIA CENTRAL) — https://itwire.com/it-industry-news/market/introducing-shopx-a-global-group-bringing-the-power-of-digital-to-physical-retail
- ShopFully, MEDIA Central and Flipp merger — https://prwire.com.au/pr/117796/shopfully-and-media-central-join-forces-with-flipp-creating-the-global-leader-in-drive-to-store-marketing
- ShopFully company page — https://linkedin.com/company/shopfully
- Flipp company page — https://linkedin.com/company/flipp
- ShopFully marketplaces — https://en.wikipedia.org/wiki/ShopFully

- Bedrock Managed Knowledge Base GA — https://aws.amazon.com/about-aws/whats-new/2026/06/amazon-bedrock-managed-knowledge-base/
- Bedrock managed KB ACL awareness — https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-acl.html
- AWS services in maintenance — https://docs.aws.amazon.com/general/latest/gr/maintenance_services.html
- Q Business availability change — https://docs.aws.amazon.com/amazonq/latest/qbusiness-ug/qbusiness-availability-change.html
- S3 Vectors GA — https://aws.amazon.com/about-aws/whats-new/2025/12/amazon-s3-vectors-generally-available
- OpenSearch Serverless cost floor — https://aws.amazon.com/blogs/big-data/amazon-opensearch-serverless-cost-effective-search-capabilities-at-any-scale
- Bedrock GraphRAG limitations — https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-build-graphs.html
- AgentCore Gateway and Policy (Cedar) — https://aws.amazon.com/blogs/security/why-policy-in-amazon-bedrock-agentcore-chose-cedar-for-securing-agentic-workflows/
- Verified Permissions for RAG — https://aws.amazon.com/blogs/architecture/secure-multi-tenant-rag-with-amazon-bedrock-and-verified-permissions/
- AWS guidance on authorizing RAG data — https://aws.amazon.com/blogs/security/authorizing-access-to-data-with-rag-implementations
- Glue zero-ETL — https://aws.amazon.com/about-aws/whats-new/2025/11/aws-glue-cloudformation-cdk-zero-etl-integrations
- Debezium CDC on MSK Connect — https://aws.amazon.com/blogs/big-data/real-time-cdc-from-aurora-postgresql-to-amazon-s3-tables-using-debezium-and-firehose/
- AgentCore Evaluations GA — https://aws.amazon.com/about-aws/whats-new/2026/03/agentcore-evaluations-generally-available
- Dropbox Dash context engine — https://www.infoq.com/news/2026/02/dropbox-context-engine
- Anthropic Contextual Retrieval — https://www.anthropic.com/engineering/contextual-retrieval
- Onyx permission sync (paid tier) — https://docs.onyx.app/admins/connectors/official/github
- Airbyte gap on disappearing records — https://github.com/airbytehq/airbyte/discussions/82094
- LocalStack auth token requirement — https://blog.localstack.cloud/the-road-ahead-for-localstack/
- MinIO archived — https://www.docker.com/blog/minio-end-of-life-how-to-stay-patched-and-audit-ready-with-docker-els/
- Neptune vs Neo4j compatibility — https://docs.aws.amazon.com/neptune/latest/userguide/migrating-from-neo4j-general.html
- MSK pricing — https://aws.amazon.com/msk/pricing/
- Cohere Embed v4 on Bedrock — https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed-v4.html
- Claude Haiku pricing — https://anthropic.com/claude/haiku
- Bedrock pricing — https://aws.amazon.com/bedrock/pricing/
- GitHub Models retired — https://docs.github.com/en/enterprise-cloud%40latest/github-models
- Copilot Student plan update — https://github.com/orgs/community/discussions/189268
- ChatGPT Plus (no API credits) — https://help.openai.com/en/articles/6950777-what-is-chatgpt-plus
- OpenCode providers — https://opencode.ai/docs/providers
- OpenCode Go — https://opencode.ai/docs/go/
