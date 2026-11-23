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
| Implementation of plan tasks | Codex (ChatGPT Plus) and Claude Code, alternating by task | Agents in the repository; both read `AGENTS.md`; shared skills in `.agents/skills/` |
| Inline completions while editing (optional) | GitHub Copilot (Student) in VS Code | Fast small edits; reads `AGENTS.md` and `.github/copilot-instructions.md` |
| Test writing | The agent that did **not** implement the step, from the port and option card | Tests that don't mirror the implementation's mistakes |
| Code review | The other tool: Claude Code's read-only `reviewer` subagent for Codex code; Codex `$cross-review` or `/review` for Claude code; then me | Cross-review across model families (OpenAI vs Anthropic) |
| Checks | ruff, mypy, import-linter, pytest, gitleaks, pip-audit, Trivy in CI | Same checks for human and AI code |

All tools read the same instructions: `AGENTS.md`. Codex and Copilot load it directly; Claude Code loads it through `CLAUDE.md` (`@AGENTS.md`). Repeatable workflows are written once as skills in `.agents/skills/` (Codex) with thin wrappers in `.claude/skills/` (Claude Code).

## 3. The loop for every task

```
plan task (docs/plan.md)
  → plan-task skill in Codex or Claude Code: the agent proposes a plan → I approve or correct it
  → agent implements + writes tests → runs format, lint, types, layer rules, tests
  → cross-review skill in the *other* tool (different model family) → I read the findings
  → I fix or reject → commit with AI-SDLC trailers → CI on push
  → if a default option changes: experiment result in evaluation/results/
```

Commit trailers (template in `.gitmessage`, enabled with `git config commit.template .gitmessage`):
```
AI-Tool: Codex + <model>   (or Claude Code + <model>)
AI-Reviewed-by: Claude Code reviewer (<model>)   (or Codex + <model>) / me
Human-changes: <what I changed in the AI output>
```

## 4. How AI was used in each phase

**Design.**
- Claude researched AWS and open-source options and checked which services were in maintenance mode.
- It compared alternatives and drafted the decision log, the architecture diagrams and the option evaluation plan.
- I set the constraints (AWS or open source only, budget of $10–25, company size), corrected the sizing and chose between the options.

**Implementation.**
- Codex or Claude Code implements one plan task at a time, inside the folder of the layer it belongs to.
- The `plan-task` skill forces a plan before any code, and `new-option` makes adding an option uniform (port, registration, contract test, unit tests).

**Testing.**
- Tests are written from the port and the option card, by the tool that did not write the implementation. This keeps them from mirroring the implementation's mistakes.
- The permission and deletion gates are deterministic tests in CI that use no models.
- Model quality is measured separately, with the frozen question set and the experiment runner.

**Review.**
- The `cross-review` skill (read-only) checks gates, layer rules, tests, model-call rules and secrets, in that order.
- The reviewer is always the other tool, so an OpenAI model reviews Anthropic code and the reverse.
- CI repeats every automated check on GitHub.

## 5. Evidence log

| Date | Task | Tool / model | What AI produced | What I decided or changed | Review |
|---|---|---|---|---|---|
| 2026-11-23 | Research and service comparison | Claude | Comparison of AWS and open-source options; found services in maintenance mode | Constraint: AWS or open source only; chose build over Onyx | n/a (design) |
| 2026-11-23 | Decision log, sizing | Claude | `decisions.md` D1–D18, risks | Corrected company size to 100–500; budget $10–25 | I reviewed each decision |
| 2026-11-23 | Option evaluation plan | Claude | `option-evaluation.md`: 19 option cards, metrics, gates | Asked for one measured comparison per choice; edited the permission and publication tests | I reviewed |
| 2026-11-23 | Architecture diagrams | Claude | Whiteboard and `architecture.md` (Mermaid) | Asked for the enterprise version with local tools | I reviewed |
| 2026-11-23 | Repository structure, CI/CD, Docker | Claude | Skeleton, layer rules, CI/CD, Dockerfile, Compose, this AI-SDLC setup | Folder-per-layer idea was mine; accepted the changes (no numbers, sources and agents outside `src/`) | Checks pass: ruff, mypy, import-linter, 16 tests |
| | | | | | |

## 6. What I'll report at the end

- Share of commits written mainly by AI, and how many I changed before merging.
- Issues the reviewer agent caught that the authoring model missed (and the reverse).
- Where AI was wrong or unhelpful, and how the guardrails caught it, or didn't.
- Time spent per task compared with the estimates in `docs/plan.md`.
- Model spend during development (from cost logs), against the $10–25 budget.
