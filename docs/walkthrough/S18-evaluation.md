# S18 · Measuring retrieval, permissions and freshness

The evaluation runner lives in architecture box 8. It loads the same synthetic HR pages as the worker, asks questions with known supporting passages, and measures how each retrieval option ranks them. Separate scenarios change a live mock source while a caller has a cached answer. This lets you observe the interval when an index contains yesterday’s permissions or a deleted page.

## Run the demo

From the repository root, run:

```powershell
uv run ekb demo evaluation
```

Alice and Lucía both get a cited mentoring policy. The source owner then limits the page to HR staff while the index still admits Alice. The report shows how many passages a pre-filter alone would release, what survives a source recheck, and what Alice receives when she asks again. Lucía still receives the policy after the source recovers. Each run creates fresh mocks and an in-memory index.

## From a question to a score

| Input | What the runner does | Evidence |
|---|---|---|
| A versioned question with expected text, chunk IDs and readers | Checks that the worker published those IDs, their text supports the label, and the reader list matches the published ACLs | A stale or inconsistent label fails the run |
| One question and one authorized identity | Looks up current principals, ranks the readable chunks, and compares their IDs with the labels | One raw result per question and mode |
| Results for the same question set | Computes recall, reciprocal rank, Hit@5 and query latency; resamples paired differences 1,000 times | A Markdown report with language and category breakdowns |
| A scripted change to the source | Queries before and after synchronization and inspects released text and citations | A gate failure makes the command exit unsuccessfully |

For example, comparing remote work in Milan and Madrid needs **two** passages. Finding Milan at rank one but missing Madrid gives Hit@5 of 1, reciprocal rank of 1, and Recall@5 of 0.5. The runner uses the first identity in each question’s allowed list for the quality run. Separate gate probes ask as someone who lacks access.

The two main calls in the quality path are:

```python
validate_labels(system, rows)
samples = evaluate(system, rows, mode, card.top_k)
```

The 80 development questions and 20 hold-out questions cover twelve synthetic pages. Each pair of phrasings tests the same fact. Hold-out pairs ask about different facts from the development pairs. Related phrasings and the small corpus limit how broadly the bootstrap intervals can be interpreted. The labels have been checked against source text and permissions and still need a human reader to judge their meaning.

## Run a comparison

| Card | Command from the repository root | What to inspect |
|---|---|---|
| 4.4 Embeddings | `uv run --extra ollama ekb-eval run evaluation/experiments/4.04-embeddings.yaml` | Local bge-m3, hybrid Recall@10 and per-language performance |
| 4.5 Retrieval | `uv run --extra ollama ekb-eval run evaluation/experiments/4.05-retrieval.yaml` | BM25, vector and reciprocal rank fusion at five results, especially the cross-language questions |
| 4.13 Permissions | `uv run ekb-eval run evaluation/experiments/4.13-permissions.yaml` | Pre-filter alone, post-filter alone, the combined path, cached answers and source timeout |
| 4.14 Propagation | `uv run ekb-eval run evaluation/experiments/4.14-propagation.yaml` | The actual webhook and full-listing paths for adds, edits, ACL changes and deletes |

For cards 4.4 and 4.5, start Ollama with `bge-m3` installed. T7 and the query encoder use the same gateway route. Card 4.5 warms the question embeddings before timing any mode. Vector search with an unavailable encoder fails the comparison. The two gate cards use the offline worker and extractive answer writer and need no model service.

`--holdout` selects the held-back questions for a final quality run. `--summary PATH` also appends the Markdown report to a file. Reports go to `evaluation/results/`; the ignored `raw/` subdirectory contains per-question rankings and timings. Two runs in the same second get distinct filenames.

Card 4.4’s `--paid` selects the hosted Bedrock options. These require AWS credentials, model access, and `langchain-aws`. The card and environment spend caps apply across routes. The gateway sums Bedrock's input-token response headers across successful batches. A provider response without billed usage leaves cost unknown and the gateway pauses further paid calls pending reconciliation. Local evidence cannot settle the choice among hosted providers. The Titan V2 price entry uses AWS’s published per-token rate. [AWS model and pricing comparison](https://aws.amazon.com/blogs/machine-learning/get-started-with-amazon-titan-text-embeddings-v2-a-new-state-of-the-art-embeddings-model-on-amazon-bedrock/)

## Local results

Across the 80 development questions, vector-only retrieval found every labeled chunk in its top five. Hybrid reached 0.831 Recall@5 and BM25 reached 0.725. The difference was largest across languages: vector search found the evidence on all 18 cross-language questions, hybrid on five and BM25 on two. This is evidence for a larger evaluation after human label review. The model and retrieval defaults were retained. [Retrieval report](../../evaluation/results/2026-11-26-121614-4.05-retrieval-9e7a02.md) · [Embedding report](../../evaluation/results/2026-11-26-122651-4.04-embeddings-6c024e.md)

A pre-filter alone would have exposed the withdrawn page. The combined check withheld it, the authorized reader recovered access after a timeout, and both propagation paths removed the superseded page. [Permission report](../../evaluation/results/2026-11-26-122416-4.13-permissions-0b94ec.md) · [Propagation report](../../evaluation/results/2026-11-26-121614-4.14-propagation-60d00f.md)

## What the gate measurements mean

**Permissions.** The runner first measures authorized Recall@5 and p95 latency against the unfiltered control on the same questions. It then changes the source. The copied ACL still admits Alice, so a positive stale-index control proves that there is material to withhold. The post-filter baseline ranks with an identity verified to see every synthetic chunk, then rechecks for Alice. That comparison stays inside the experiment. The combined path exercises real assistant replies, including a warm answer, a fresh question, an unavailable source, and a restricted salary question.

**Propagation.** The runner observes a new page inside the T8 publish call, after T7 produced vectors. It checks that the search store still lacks the page, then lets publication proceed. It replays an unchanged delivery to count skipped processing. It checks edit text, readers, a deleted travel policy and an HR leaver whose deletion only a full listing detects. The timings cover one active mutation-and-sync cycle per change type. The 1-, 5- and 15-minute polling waits are calculated for uniformly timed arrivals. These are local scenario measurements, with four samples per connector path.

## Code and tests

| Location | Responsibility |
|---|---|
| `src/ekb/app/eval_world.py` | Create isolated sources and stores, wire the worker, and restrict the gateway to the selected embedding route |
| `src/ekb/evaluation/quality.py` | Read questions, validate labels, rank evidence and calculate paired metrics |
| `src/ekb/evaluation/scenarios.py` | Apply source changes and inspect publication, release, deletion and reprocessing |
| `src/ekb/evaluation/runner.py` and `cli.py` | Validate a card, run the comparison and save evidence |
| `tests/unit/evaluation/` and `tests/unit/test_eval_cli.py` | Verify frozen labels and splits, reject invalid input, exercise the live gates and preserve failed-run evidence |
| `tests/gates/` | Exercise the underlying revocation, publication and deletion contracts across store implementations |

## Check your understanding

1. Why can Hit@5 be perfect when Recall@5 is only 0.5?
2. Why warm the cache **before** revoking access?
3. Which parts of the five-minute polling result were actually timed?

<details>
<summary>Answers</summary>

1. Hit@5 needs one relevant chunk. Recall@5 needs both chunks for a two-source answer.
2. This gives the assistant a previously authorized answer that it could accidentally reuse. A fresh request alone cannot exercise that failure.
3. The runner timed the source change and active connector/worker processing. The expected wait until the next scheduled poll comes from the polling interval and the assumed distribution of arrival times.

</details>
