# AI-SDLC: how AI was used to design, build, test and review this project

> Deliverable 2 asks for "the process you followed and your AI-SDLC". This file describes the process and keeps the evidence log. Update the log as you work. It's what makes the description believable.

## 1. Principles

1. **AI does the work, a human makes the decisions.** AI researches, drafts, writes code and tests, and reviews. I choose the architecture, accept or reject each change, and own the result.
2. **Guardrails are code, not good intentions.** The rules the AI must follow are written once (`AGENTS.md`) and enforced automatically: layer rules (import-linter), contract tests, strict types, CI, and the permission and deletion gates. An AI change that breaks a rule fails the build, whoever wrote it.
3. **No tool reviews its own work.** Code written by one model is reviewed by a different model or by me before it is merged.
4. **Evidence over claims.** Design choices are backed by option experiments (`docs/option-evaluation.md`). AI involvement is recorded in commit trailers and in the log below.

## 2. Tools and where each is used

| Stage | Tool | Why this one |
|---|---|---|
| Research, design, architecture, decision log, option evaluation plan, repository structure | Claude (chat) | Long-context reasoning, web research with sources, diagrams |
| Implementation of plan tasks | Claude Code (`/plan-task`) | Writes the code and the tests; reads `AGENTS.md` through `CLAUDE.md` |
| Inline completions while editing (optional) | GitHub Copilot (Student) in VS Code | Fast small edits; reads `AGENTS.md` and `.github/copilot-instructions.md` |
| Test writing | Claude Code writes the tests from the port and option card; Codex checks in review that failure cases and gates are covered | Tests are checked by a model that didn't write them |
| Code review | Codex (ChatGPT Plus) with the read-only `cross-review` skill; then me | A different model family (OpenAI) reviews Anthropic-written code |
| Checks | ruff, mypy, import-linter, pytest, gitleaks, pip-audit, Trivy in CI | Same checks for human and AI code |

All tools read the same instructions: `AGENTS.md`. Codex and Copilot load it directly; Claude Code loads it through `CLAUDE.md` (`@AGENTS.md`). Repeatable workflows are written once as skills in `.agents/skills/` (Codex) with thin wrappers in `.claude/skills/` (Claude Code).

## 3. The loop for every task

```
plan task (docs/plan.md)
  → Claude Code: /plan-task <n> → proposes a plan → I approve or correct it
  → Claude Code implements + writes tests → runs format, lint, types, layer rules, tests
  → Codex: $cross-review → findings → I decide which to fix → Claude Code fixes → repeat until "approve"
  → commit with AI-SDLC trailers → git push → CI on GitHub
  → if a default option changes: experiment result in evaluation/results/
```

Commit trailers (template in `.gitmessage`, enabled with `git config commit.template .gitmessage`):
```
AI-Tool: Claude Code + <model>
AI-Reviewed-by: Codex + <model>
Human-changes: <what I changed in the AI output>
```

## 4. How AI was used in each phase

**Design.**
- Claude researched AWS and open-source options and checked which services were in maintenance mode.
- It compared alternatives and drafted the decision log, the architecture diagrams and the option evaluation plan.
- I set the constraints (AWS or open source only, budget of $10–25, company size), corrected the sizing and chose between the options.

**Implementation.**
- Claude Code implements one plan task at a time, inside the folder of the layer it belongs to.
- The `plan-task` skill forces a plan before any code, and `new-option` makes adding an option uniform (port, registration, contract test, unit tests).

**Testing.**
- Claude Code writes tests from the port and the option card, not from its own implementation; Codex checks in review that failure cases and the gates are covered.
- The permission and deletion gates are deterministic tests in CI that use no models.
- Model quality is measured separately, with the frozen question set and the experiment runner.

**Review.**
- The `cross-review` skill (read-only) checks gates, layer rules, tests, model-call rules and secrets, in that order.
- The reviewer is always Codex, so an OpenAI model reviews code an Anthropic model wrote. Nothing is pushed without its approval.
- CI repeats every automated check on GitHub.

## 5. Evidence log

| Date | Task | Tool / model | What AI produced | What I decided or changed | Review |
|---|---|---|---|---|---|
| 2026-11-23 | Research and service comparison | Claude | Comparison of AWS and open-source options; found services in maintenance mode | Constraint: AWS or open source only; chose build over Onyx | n/a (design) |
| 2026-11-23 | Decision log, sizing | Claude | `decisions.md` D1–D18, risks | Corrected company size to 100–500; budget $10–25 | I reviewed each decision |
| 2026-11-23 | Option evaluation plan | Claude | `option-evaluation.md`: 19 option cards, metrics, gates | Asked for one measured comparison per choice; edited the permission and publication tests | I reviewed |
| 2026-11-23 | Architecture diagrams | Claude | Whiteboard and `architecture.md` (Mermaid) | Asked for the enterprise version with local tools | I reviewed |
| 2026-11-23 | Repository structure, CI/CD, Docker | Claude | Skeleton, layer rules, CI/CD, Dockerfile, Compose, this AI-SDLC setup | Folder-per-layer idea was mine; accepted the changes (no numbers, sources and agents outside `src/`) | Checks pass: ruff, mypy, import-linter, 16 tests |
| 2026-11-23 | S1–S1d mock sources | Claude Code (implementation), Codex + GPT-6 (review) | Four synthetic sources, CLI demos, walkthroughs and tests | Approved the reviewed changes for merge | Codex fixed seven findings, including a cursor race that could skip deletions and revocations. All 147 tests, five import contracts, Ruff, mypy and four demos passed. |
| 2026-11-24 | S2, S2a and S2b | Claude Code (implementation), Codex + GPT-6 (review) | Six connectors, T1 normalization, local inspector API and UI, demos and walkthroughs | Approved increasing revisions for recreated pages, delete and create events for key changes, and immediate leave ACL reconciliation when ownership changes | Codex fixed nine findings. All 265 Python tests, six import contracts, Ruff, mypy, both T1 demos and the clean UI check passed. |
| 2026-11-24 | S3 change log | Claude Code (implementation), Codex + GPT-6 (review) | In-memory event bus, document registry, raw store, inspector panel and replay demo | Approved applying ACL changes immediately and using the next full listing to recover missing content or record a delete | Codex fixed five findings. All 326 Python tests, both UI tests, six import contracts, Ruff, mypy, UI build and lint passed. Replay rebuilt the same registry from 16 events and 14 originals. |
| 2026-11-24 | S4 T2 diff | Claude Code (implementation), Codex + GPT-6 (review) | T2 routing, inspector decision panel, demo and walkthrough | Approved tracking originals by document lifetime so delete retries preserve a re-created document | Codex fixed three findings covering purge order, delete retries and combined person and ACL changes. All 374 Python tests, five UI tests and six import contracts passed. The demo drained all 19 events. |
| 2026-11-24 | S5 T3 parse | Claude Code (implementation), Codex + GPT-6 (review) | Markdown and plain-text parsers, parsed document inspector panel, demo and walkthrough | Chose the published document lifetime to hide deleted text even when processing is blocked | Codex fixed six findings. All 453 Python tests, 10 UI tests, six import contracts, Ruff, mypy, UI lint and build passed. The deletion test covers re-creation with the same key and version. |
| 2026-11-24 | S6 T4 chunk | Claude Code (implementation), Codex + GPT-6 (review) | Structure-aware and fixed-window chunkers, inspector panel, demo and walkthrough | Chose to move a list together when it fits in a fresh chunk | Codex fixed list splitting and the explanation of continuation chunks. All 540 Python tests, 14 UI tests, six import contracts, Ruff, mypy, UI lint and build passed. The default demo kept 41 chunks after the edit and delete, with zero publishable. |
| 2026-11-25 | S7 model gateway | Claude Code (implementation), Codex + GPT-6 (review) | Per-task routing and failover, document-tagged cache, quota and cost log, inspector panel, demo and walkthrough | Required cross-process guards, a hardware feasibility experiment and a pause on paid calls while a charge awaits reconciliation | Codex fixed eight findings. The 684-test Python suite and the additional CLI regression passed, along with 20 UI tests, seven import contracts, Ruff, mypy, UI lint and build. The live demo failed over from Google to Groq and reused a cached reply after restart. |

## 6. What I'll report at the end

- Share of commits written mainly by AI, and how many I changed before merging.
- Issues the reviewer agent caught that the authoring model missed (and the reverse).
- Where AI was wrong or unhelpful, and how the guardrails caught it, or didn't.
- Time spent per task compared with the estimates in `docs/plan.md`.
- Model spend during development (from cost logs), against the $10–25 budget.
