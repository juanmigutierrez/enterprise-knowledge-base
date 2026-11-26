# Evaluation — data, experiments and results (layer 8 · Operate, quality part)

The runner code lives in `src/ekb/evaluation/`; this folder holds what it reads and writes.

| Folder | Contents | In git? |
|---|---|---|
| `datasets/` | Frozen question sets (`golden_v1.jsonl`) and the 20% hold-out (`holdout_v1.jsonl`). Changing a file means a new version number. | Yes |
| `experiments/` | One YAML per option card in `docs/option-evaluation.md`, named `<card>-<topic>.yaml`. | Yes |
| `results/` | One Markdown summary per run, from the results template. Raw outputs go to `results/raw/`. | Summaries yes, raw no |

Run one: `uv run --extra ollama ekb-eval run evaluation/experiments/4.04-embeddings.yaml`
(or the `eval` workflow on GitHub, which uses Bedrock through OIDC).

A default in `config/default.yaml` changes only with a linked result file in the pull request.

The [S18 walkthrough](../docs/walkthrough/S18-evaluation.md) lists all four commands, model requirements, hold-out usage and report fields. The default local embedding comparison uses Ollama. The manual Bedrock embedding workflow explicitly enables hosted routes.
