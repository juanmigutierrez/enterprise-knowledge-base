# Repository structure and engineering practices

> How the code is organized, why, and what keeps it that way. Companion to `architecture.md` (what the system is) and `option-evaluation.md` (how each choice is tested).

## 1. The rule: the folders follow the architecture diagram

Each numbered layer in the diagram has one home. Someone who has seen the diagram can find any piece of code without searching, and a reviewer can see from a file path which layer it belongs to.

```
enterprise-knowledge-base/
├── sources/                  1 · Mock department systems (outside the product)
├── src/ekb/
│   ├── contracts/            Shared models: ChangeEvent (T1), Chunk, Acl, …
│   ├── platform/             Option registry, config, model gateway, telemetry
│   ├── connect/              2 · Connectors + T1 normalize
│   │   └── options/              webhook, delta polling, full listing, identity sync
│   ├── changelog/            3 · Change log and raw store
│   │   ├── bus/                  Kafka (local) · SQS (AWS)
│   │   ├── registry/             DynamoDB document registry
│   │   └── rawstore/             S3 API (SeaweedFS locally)
│   ├── process/              4 · Worker pipeline
│   │   ├── t2_diff/  t3_parse/  t4_chunk/  t5_classify/
│   │   └── t6_entities/  t7_embed/  t8_permissions/
│   │         each: port.py + options/
│   ├── stores/               5 · Knowledge stores
│   │   └── search/  entities/  metrics/  policies/  permissions/
│   ├── access/               6 · Agent access layer
│   │   ├── auth/                 token check, on-behalf-of, Cedar (default deny)
│   │   ├── query/                q1_plan/  q2_prefilter/  q3_retrieve/  q4_postcheck/
│   │   └── mcp/                  MCP server: search, get_document, get_entity, …
│   ├── operate/              8 · Freshness monitors, reconciliation, health
│   ├── evaluation/           8 · Experiment runner, metrics, gates (code)
│   └── app/                  Composition root: CLI entry points, wiring
├── agents/                   7 · Example agent and MCP client configs (outside the product)
├── ui/                       8 · Inspector UI: React + TypeScript (Vite), local only (D19)
├── evaluation/               8 · Datasets, experiment files, results (data)
├── config/                   Chosen option per step; profiles: local · ci · bedrock
├── deploy/
│   ├── docker/Dockerfile         One image for connector, worker and API
│   └── aws/                      Lean AWS profile (optional, D18)
├── tests/
│   ├── unit/                     Mirrors src/ekb
│   ├── contract/                 Every option satisfies its step's port
│   └── (integration/, e2e/)      Added with the first infrastructure code
├── docs/                     Design document sources, plan, this file
├── compose.yaml              Local infrastructure (profiles: models, app, …)
├── pyproject.toml · uv.lock  Dependencies, tool config, layer rules
├── Makefile                  Shortcuts; every target is a plain command
├── AGENTS.md                 Rules for AI coding tools
└── .github/                  CI, eval, release workflows; Dependabot; PR template
```

### Where I followed your proposal and where I changed it

- **Followed:** one folder per numbered layer, with sub-folders per step (T2–T8, Q1–Q4, one per store), so the code follows the diagram.
- **Changed — no numbers in folder names.** Python packages cannot start with a digit (`import 4_process` is a syntax error), and numbers would make renumbering the diagram a code change. The number is in each package's docstring, in the table above and in `AGENTS.md`. Step folders keep their T/Q prefix (`t7_embed`) because those labels are stable and sort in pipeline order.
- **Changed — layers 1 and 7 sit outside `src/`.** Mock sources and example agents are not the product; they are what the product talks to. Keeping them out means the product cannot accidentally depend on a mock, and the production image doesn't contain them.
- **Changed — layer 8 is split into code and data.** The runner and metrics are code (`src/ekb/evaluation/`, typed and tested); the questions, experiment files and results are data (`evaluation/`), versioned separately and reviewable without reading Python.
- **Added — `contracts/`, `platform/`, `app/`.** Every layer needs the shared models and the plumbing, and someone has to wire the options together. Giving those their own home keeps them out of the layers.

## 2. How "try several options per step" is built into the code

This is what makes the option evaluation plan possible without rewriting code per experiment.

1. **Port.** Each step has `port.py`, a `typing.Protocol`. Example: `process/t7_embed/port.py` defines `Embedder` with `model_id`, `dimensions` and `embed(texts)`.
2. **Options.** Each implementation is one file in `options/`, registered by name:
   ```python
   @register("t7_embed", "model")
   class ModelEmbedder: ...
   ```
   An option that calls a model names a gateway task (`embed`); which model answers it is configuration (`gateway: tasks:`), so swapping bge-m3 for Cohere Embed v4 is a config change, not a new option (S10).
3. **Config chooses.** `config/default.yaml` names the starting option per step and links its option card. Profiles override it: `ci` (no models, deterministic), `bedrock` (recommended models).
4. **Experiments compare.** `evaluation/experiments/<card>-<topic>.yaml` fixes every other step, lists the options to compare, the gates, the primary metric and the decision rule (cheapest option within 2 points of the best). The runner builds each option through the same registry and writes a result file.
5. **Evidence changes defaults.** A pull request that changes `config/default.yaml` must link the result file (PR template).
6. **Every option is checked automatically.** `tests/contract/test_ports.py` finds every registered option and checks it implements its port, so a new option can't silently break the pipeline.

`uv run ekb options` prints, for the active profile, the chosen option per step, its option card, and whether it is built yet.

## 3. Rules that are enforced, not just written

| Rule | Enforced by |
|---|---|
| Layers only depend downward: evaluation → app → stages (connect, process, access, operate) → infrastructure (changelog, stores) → platform → contracts | `import-linter` layers contract, in CI and pre-commit |
| Steps T2–T8 never import each other; same for Q1–Q4 and for the stores | `import-linter` independence contracts |
| Every option implements its port | `tests/contract/` |
| Only the gateway's LangChain option imports LangChain; steps call the gateway (S7) | `import-linter` forbidden contract |
| A change event is valid (an upsert points to the original; a delete carries no content) | Pydantic validator in `contracts/events.py` + unit tests |
| No chunk is publishable before ACL, sensitivity and vector are set | `Chunk.publishable` in `contracts/knowledge.py`; publication test (card 4.14) |
| Formatting, lint, security lint (bandit rules), strict types | ruff, mypy strict |
| Coverage ≥ 80% | pytest-cov `fail_under` |
| No secrets committed | gitleaks (pre-commit and CI), `.gitignore`, `.env.example` only |

Running `uv run lint-imports` on the current skeleton: **4 contracts kept, 0 broken.**

## 4. Docker

- **One image, several roles.** `deploy/docker/Dockerfile` builds one image; Compose runs it as `ekb connect`, `ekb worker` and `ekb api`. This matches "same container image" in the architecture and halves build and scan time.
- **Multi-stage build with uv.** Dependencies are installed from `uv.lock` in their own cached layer, so code changes rebuild in seconds. The runtime stage has no build tools.
- **Hardening.** Non-root user (UID 10001), no bytecode or pip caches, `.dockerignore` as an allow-list (only `pyproject.toml`, `uv.lock`, `README.md`, `src/`, `config/` enter the build).
- **Optional heavy dependencies** (Docling, AWS SDK) are installed through `--build-arg EXTRAS=...`, so the base image stays small.
- **Compose profiles.** `docker compose up` starts the infrastructure core (Kafka, OpenSearch, DynamoDB Local); `--profile models` adds Ollama; `--profile app` adds our services; `sources`, `full` and `observability` profiles are added as those parts are built. Ports bind to `127.0.0.1` only, images are pinned to versions, and services have health checks so `--wait` is reliable.

## 5. CI/CD

| Workflow | When | What |
|---|---|---|
| `ci.yml` › quality | Every PR and push to main | ruff format and lint, mypy strict, import-linter layer rules |
| `ci.yml` › test | Every PR | Unit and contract tests, coverage ≥ 80%, JUnit + coverage reports uploaded |
| `ci.yml` › security | Every PR | gitleaks over the full history; pip-audit on locked dependencies |
| `ci.yml` › image | After quality and test | Build with layer cache, smoke run, Trivy scan (fails on fixable critical/high) |
| `ci.yml` › integration | After quality and test | Starts Compose infrastructure; integration tests and the **permission and deletion gates** with the `ci` profile (no models, free, deterministic) |
| `eval.yml` | Manual only | Runs one option experiment with Bedrock through **GitHub OIDC** (no stored AWS keys), with a spend cap; uploads the result |
| `release.yml` | Tag `v*.*.*` | Multi-arch image to GHCR with **SBOM and build provenance**; GitHub release with generated notes |
| `dependabot.yml` | Weekly | uv, GitHub Actions, Dockerfile and Compose image updates |

Supply-chain choices worth stating in the interview:
- **Actions pinned to commit SHAs**, with the version tag in a comment. In March 2026 an attacker force-pushed 76 of 77 version tags of `aquasecurity/trivy-action` to credential-stealing code; pinning by SHA is what protects a pipeline from that. Dependabot keeps the SHAs current.
- **Least-privilege tokens.** Workflows default to `contents: read`; only the release job can write packages and releases.
- **Locked dependencies** (`uv.lock`, `--locked` everywhere) and a vulnerability scan of both the dependencies and the final image.
- **Model spend is not in the PR path.** PR checks never call a paid model; experiments run on demand with a budget.

Suggested repository settings (done once in GitHub): protect `main`, require the `ci` checks and one review, squash-merge with conventional commit titles, and create an `eval` environment with a required reviewer so experiments that spend money need an approval.

## 6. Local developer workflow

```
make setup        # uv sync + pre-commit hooks
make check        # what CI runs: lint, types, layer rules, tests
make up           # infrastructure core
make options      # which option each step uses, and which are built
```
On Windows without `make`, run the command behind each target (`make help` lists them). `.gitattributes` forces LF line endings so files edited on Windows behave the same in Linux containers.

## 7. Known limits of the skeleton

- Only the pattern is implemented end to end: contracts, registry, config, CLI and one option (`t7_embed: fake`). Other steps have their folder and docstring; their ports are written as each step is built (plan §4.2).
- The image build could not be tested from the environment that wrote this file because Docker Hub was not reachable; it runs in the `image` CI job on the first push.
- `amazon/dynamodb-local` is still on `latest`; pin it after the first pull.
- The integration job tolerates "no tests collected" until the first integration test lands.
