# Option Evaluation Plan

> Companion to `decisions.md`. For every part of the system: which options we compare, how we measure them, what matters most, and how we pick a winner — so each choice in the document is backed by evidence and the rejected options are rejected for a measured reason.
> Written 23 November 2026, sized for ShopFully / Flipp (~100–500 employees).

---

## 1. Principles

1. **Same test, same data, every option.** All options run on the same frozen question set and corpus, so differences come from the option, not the data.
2. **Hard gates before scores.** Some properties are pass/fail (no permission leaks, deleted content never returned). An option that fails a gate is out, however good its scores.
3. **Quality first, then cost, then speed.** Among the options that pass the gates, pick the **cheapest option within a small tolerance of the best quality** (default: within 2 percentage points on the main metric). A 0.5-point gain is rarely worth 10× the price.
4. **Be honest about noise.** With ~100 questions, differences under ~5 points can be chance. Compare options on the same questions (paired), report bootstrap confidence intervals, and call a tie a tie.
5. **Don't tune on the test.** Keep 20% of the questions as a hold-out that is only used for the final comparison.
6. **Measure what can be measured; reason about the rest.** The 4–6 hour budget can't cover everything. Each option card below says whether it is tested **in the POC (P1)**, **in an AWS dev account later (P2)**, or **decided on paper with explicit criteria (P3)**.

---

## 2. The test set (golden dataset)

About **100 questions** over the POC corpus (~300 HR / company documents, synthetic data), each with: the expected answer, the IDs of the chunks that contain it, and which test users may see it.

| Category | Example | Why it's there |
|---|---|---|
| Simple lookup | "How many days of parental leave do I get in Italy?" | Baseline |
| Exact term | "What does policy HR-PL-07 say?" / a retailer or campaign code | Keyword search matters here |
| Paraphrase | "Can I take time off when my baby is born?" | Vector search matters here |
| **Multilingual** | Question in English, answer in an Italian or Spanish document | ShopFully works in IT, ES, DE, FR, PT, PL and EN; Flipp in EN and FR |
| Multi-document | "Compare remote-work rules in Milan and Madrid" | Retrieval breadth, answer synthesis |
| Freshness | Asked after a policy was edited | Must return the new version |
| **Deleted content** | Asked about a document that was deleted | Must not answer from it (gate) |
| **Permission-restricted** | A user without access asks about salary bands | Must not leak (gate) |
| Not in the knowledge base | "What is the company's stock price?" | Must say it doesn't know |

Rules: write the questions **before** building retrieval; questions are created with a strong model and **checked by a human**; 20 of them are the hold-out.

---

## 3. Metrics — what each one tells you

| Metric | Measures | Why it matters | Needs an LLM? |
|---|---|---|---|
| **Hit@k** | Share of answerable questions with at least one relevant chunk in the top k | Did retrieval find any supporting evidence? | No |
| **Recall@k** | Fraction of labelled relevant chunks in the top k, averaged over answerable questions | Did retrieval cover all the evidence for multi-document answers? | No |
| **MRR / nDCG@k** | How high the correct chunk ranks | Fewer, better chunks → cheaper and more accurate answers | No |
| **Context precision** | Share of retrieved chunks that are relevant | Noise in the context causes wrong answers | Yes (judge) |
| **Faithfulness** | Are the answer's claims supported by the retrieved chunks? | Catches hallucination | Yes (judge) |
| **Answer correctness** | Does the answer match the expected answer? | What users experience | Yes (judge) |
| **Citation accuracy** | Do the cited sources actually support the claim? | Trust and auditability | Yes (judge) or rule |
| **Correct refusal rate** | Says "I don't know" / "not allowed" when it should | Unanswerable and restricted questions | Partly |
| **Leakage rate** (gate = 0) | Restricted content returned to a user without access | Security | No |
| **Deleted-content rate** (gate = 0) | Deleted content returned | Self-maintenance | No |
| **Source-to-index lag** p50 / p95 | Time from a change in a source to being searchable | Freshness requirement | No |
| **Permission sync lag** p95 | Time from a source revocation to the index permission update | Tracks the five-minute sync target. The next request after revocation must already withhold the document. | No |
| **JSON validity** (gate ≥ 99%) | Structured outputs parse | Pipeline reliability | No |
| **Entity F1** | Entities found vs a hand-labelled set | Graph / entity store quality | No (labelled set) |
| **Latency** p50 / p95 | Per step and end to end | Agent responsiveness | No |
| **Cost** | Per 1,000 documents ingested and per 1,000 queries | Budget | No |

**Judge reliability:** before trusting an LLM judge, compare it with human labels on 30 answers. Use it only if agreement is high (Cohen's kappa ≥ 0.6); report the agreement in the document.

---

## 4. Option cards

Each card: options → what to measure → what matters most / watch-outs → priority → starting choice (to be confirmed by the results).

### 4.1 Parsing (T3)
- **Options:** plain text extraction (pypdf) · **Docling** · Bedrock Data Automation (on a sample only).
- **Measure:** downstream Recall@5; manual check of tables and headings on 10 hard documents; cost and time per page.
- **What matters:** tables and headings survive (they drive chunking); scanned documents are a separate class — measure them separately.
- **Priority:** P1 (Docling vs plain text); Data Automation P2. **Start with:** Docling.

### 4.2 Chunking (T4)
- **Options:** fixed 400 tokens with overlap · **structure-aware** (split on headings and sections) · semantic splitting.
- **Measure:** Recall@5, nDCG@5, number of chunks (drives cost), faithfulness on a sample.
- **What matters:** policies are structured documents, so section boundaries usually beat fixed windows; chunk count multiplies every later cost.
- **Priority:** P1. **Start with:** structure-aware.

### 4.3 Context header per chunk (T4)
- **Options:** none · Claude Haiku 5.5 · Claude Sonnet 5.5.
- **Measure:** Recall@5 gain over "none"; cost per 1,000 chunks.
- **What matters:** the gain is largest for chunks that don't name their subject ("the employee is entitled to…"). If Haiku is within tolerance of Sonnet, Haiku wins on cost.
- **Priority:** P1 (none vs Haiku); Sonnet on a 200-chunk sample. **Start with:** Haiku.

### 4.4 Embeddings (T7)
- **Options:** **Cohere Embed v4** (1024 dimensions, float vs int8) · Cohere Embed Multilingual · Amazon Titan Text Embeddings V2 · Amazon Nova Multimodal Embeddings · bge-m3 via Ollama (local, free).
- **Measure:** Recall@5 and MRR **per language** and for **cross-lingual** questions; storage per vector; embedding cost; latency.
- **What matters:** multilingual and cross-lingual quality is the deciding factor for ShopFully / Flipp; int8 should lose almost nothing vs float — verify it. Changing model later means re-embedding everything, so this choice is sticky.
- **Priority:** P1 — embeddings are cheap, so this is the easiest high-value experiment. **Start with:** Cohere Embed v4, int8.

### 4.5 Retrieval mode (Q3)
- **Options:** BM25 only · vector only · **hybrid** (reciprocal rank fusion) · hybrid with tuned weights.
- **Measure:** Recall@k and MRR, **broken down by question category** (exact term vs paraphrase vs multilingual).
- **What matters:** look for categories where one mode fails completely; hybrid should never be much worse than the best single mode.
- **Priority:** P1 (no LLM cost). **Start with:** hybrid.

### 4.6 Reranking (Q3)
- **Options:** none · Cohere Rerank 3.5 · Amazon Rerank · bge-reranker (local).
- **Measure:** nDCG@5 and precision@5 gain; added p95 latency; cost per 1,000 queries.
- **What matters:** the reranker's value is letting you send fewer chunks to the answering model (see 4.7) — measure both together.
- **Priority:** P1. **Start with:** Cohere Rerank 3.5.

### 4.7 How many chunks to send (Q3 → answer)
- **Options:** top 3 · top 5 · top 10.
- **Measure:** answer correctness, faithfulness, input tokens per answer.
- **What matters:** more context is not always better; extra chunks add noise and cost.
- **Priority:** P1. **Start with:** top 5.

### 4.8 Query planning and rewriting (Q1)
- **Options:** none · Haiku rewrite (expand acronyms, translate, split multi-part questions).
- **Measure:** Recall@5 gain overall and for multilingual and multi-document questions; added latency.
- **What matters:** often helps cross-lingual and multi-part questions, adds a model call per query.
- **Priority:** P1 if time allows, otherwise P2. **Start with:** Haiku rewrite only for multi-part or non-English questions.

### 4.9 Classification: department, sensitivity, PII (T5)
- **Options:** rules only (Presidio + keywords) · Haiku only · **Haiku + rules**.
- **Measure:** accuracy on 100 labelled documents; **false-negative rate for "confidential"** weighted heavily (a missed confidential label is a security incident; an extra label is just friction).
- **What matters:** asymmetric error cost — choose the option with the lowest false negatives, then cost.
- **Priority:** P1 on a small labelled set. **Start with:** Haiku + rules.

### 4.10 Entity extraction and resolution (T6)
- **Options:** Haiku 5.5 · Sonnet 5.5 · gpt-oss-120b on Bedrock · **cascade** (exact match on HR data → Haiku → Sonnet when unsure).
- **Measure:** entity precision, recall and F1 vs 50 hand-labelled chunks; duplicate-entity rate after resolution; JSON validity; cost per 1,000 chunks.
- **What matters:** precision matters more than recall (a wrong merge of two people corrupts the store); JSON validity is a gate.
- **Priority:** P1 on 50 chunks. **Start with:** cascade.

### 4.11 Answer generation (agent side)
- **Options:** Haiku 5.5 · Sonnet 5.5 · Opus 5.5 · gpt-oss-120b on Bedrock.
- **Measure:** answer correctness, faithfulness, citation accuracy, correct refusal rate, p95 latency, cost per answer.
- **What matters:** refusals and citations matter as much as correctness for an HR assistant; check whether Opus's gain justifies roughly 2× Sonnet's price.
- **Priority:** P1 on 50 questions. **Start with:** Sonnet 5.5.

### 4.12 Judge model (evaluation)
- **Options:** Sonnet 5.5 · Opus 5.5.
- **Measure:** agreement with 30 human labels (kappa); cost per evaluation run.
- **What matters:** a cheap judge that disagrees with humans makes every other result meaningless — check it first.
- **Priority:** P1 (do this before the other LLM experiments). **Start with:** whichever agrees with humans; Sonnet if both do.

### 4.13 Permission enforcement (Q2, Q4)
- **Options:** pre-filter only · post-filter only · **pre-filter + re-check of top results**.
- **Measure:** **leakage rate (gate = 0)** on the restricted questions plus adversarial ones (prompt injection asking for other users' data); recall loss vs no filtering; added latency; revocation lag.
- **What matters:** post-filter only can return empty or thin answers; pre-filter only relies on how fresh the copied permissions are.
- **Acceptance test:** Ask as an authorized employee and warm the answer cache. Revoke access in the mock source while leaving the index stale. Repeat the question through both cached and uncached paths. Neither response may reveal the withdrawn policy, title or citation. Repeat with a source timeout. A second employee who still has access should get the cited policy when the source is available.
- **Priority:** P1. Use the pre-filter and current access check together. The other modes are comparison baselines.

### 4.14 Change propagation (T1, T2)
- **Options:** webhooks · polling every 1 / 5 / 15 minutes · nightly full listing (always on, for deletes).
- **Measure:** source-to-index lag p50/p95; deleted-content rate (gate = 0); share of deletes caught only by the nightly listing; cost of unchanged re-processing avoided by hash checks.
- **Publication test:** Pause ingestion after embedding and before permissions are attached. The new chunk must be absent from search. Resume processing and verify that the chunk becomes searchable only for an authorized user. Measure deletes and permission changes separately from edits.
- **Priority:** P1 (scripted scenarios: add, edit, delete, revoke). **Start with:** webhooks + 5-minute polling fallback + nightly listing.

### 4.15 Event backbone (D4)
- **Options:** EventBridge + SQS FIFO · Amazon MSK · Kinesis Data Streams.
- **Criteria:** monthly cost at 10k / 50k / 1M changes per day; per-document ordering; replay; operations effort; scale ceiling.
- **How:** cost model (spreadsheet) now; load test in an AWS dev account later.
- **Priority:** P3 now, P2 later. **Start with:** EventBridge + SQS.

### 4.16 Search store (D5)
- **Options:** OpenSearch Serverless (new generation) · OpenSearch managed domain · Aurora PostgreSQL + pgvector · S3 Vectors.
- **Criteria:** hybrid quality (from 4.5), p95 latency at 10M chunks with permission filters for users in many groups, monthly cost at idle and at peak.
- **How:** quality from the POC; latency and cost from a load test in AWS dev.
- **Priority:** P2. **Start with:** OpenSearch Serverless (new generation).

### 4.17 Entity store vs graph (D6)
- **Options:** DynamoDB entity store · Neptune Database.
- **Criteria:** share of real questions that need multi-hop relationships (count them in the test set and in early usage logs); cost; query latency.
- **Priority:** P3 now; revisit with usage data. **Start with:** entity store.

### 4.18 Authorization engine (D8)
- **Options:** Verified Permissions (Cedar) · OpenFGA.
- **Criteria:** do we need "list everything user X can see"? latency per batch check; policy authoring and audit.
- **Priority:** P3. **Start with:** Cedar (the same files run locally and in Verified Permissions).

### 4.19 Managed RAG bake-off (D1)
- **Options:** our index · Bedrock Managed Knowledge Base (for the sources it supports).
- **Measure:** the same retrieval and answer metrics, plus leakage, freshness lag and setup effort, on the same questions.
- **Priority:** P2 (needs AWS). **Start with:** our index.

---

## 5. What we run in the POC, and what it costs

Only the **P1** experiments. Everything uses the frozen test set, the content-hash cache and batch inference where possible.

| Experiment | LLM calls? | Estimated cost |
|---|---|---|
| Judge vs human labels (30 answers) | Yes | < $1 |
| Parsing, chunking, retrieval mode, top-k | No (retrieval metrics only) | ~$0 |
| Embeddings (5 options × ~1,500 chunks) | Embeddings only | < $1 |
| Reranking (3 options × 100 questions) | Rerank calls | < $1 |
| Context header (none vs Haiku; Sonnet on 200 chunks) | Yes | ~$1 |
| Classification and entity extraction (100 docs, 50 chunks, 3–4 models) | Yes | ~$1–2 |
| Answer models (4 models × 50 questions) + judging | Yes | ~$5–8 |
| **Total for the experiments** | | **~$10–15** |

Together with the build itself, the total stays at about **$20–40**. Keeping to the original **$10–25** is possible by running the answer-model comparison on 30 questions and skipping Opus as an answer candidate.

**Time:** the retrieval experiments (4.1–4.7) are mostly configuration once the test harness exists — about 1–2 hours. The LLM comparisons add about 1 hour. If time runs short, keep 4.4 (embeddings), 4.5 (retrieval mode), 4.13 (permissions) and 4.14 (propagation): they answer the brief's core requirements. Mark the rest as planned.

---

## 6. Results template (fill one per experiment)

```markdown
### Experiment: <name>   Date: <yyyy-mm-dd>   Data: test set v<n> (<N> questions, hold-out excluded)

| Option | Gate(s) passed | Main metric (95% CI) | Secondary metrics | Cost | p95 latency |
|---|---|---|---|---|---|
| A | ✅ | 0.82 (0.76–0.88) | … | $… | … ms |
| B | ✅ | 0.80 (0.73–0.86) | … | $… | … ms |
| C | ❌ leak | — | — | — | — |

**Winner:** B — within 2 points of A at a third of the cost.
**Why the others lost:** A: +2 points not worth 3× cost. C: failed the leakage gate.
**Watch-outs / what would change the decision:** …
```

Each result then updates the matching decision in `decisions.md` ("Start with" → "Chosen, based on experiment X").
