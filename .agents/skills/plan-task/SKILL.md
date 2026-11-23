---
name: plan-task
description: Build one step (S1, S2, ...) from section 4.2 of docs/plan.md in this repository, plan first, then code, tests, a demo and a walkthrough that teaches the user how it works. Use when asked to do "step S<n>", "task <n>" or to implement a plan item.
---

# Build one plan step

The user gives a step ID from the table in `docs/plan.md` §4.2 (for example `S2`). Build **only that step**. The user is learning the system step by step, so clarity matters as much as working code.

## 1. Read
`AGENTS.md`, `docs/repo-structure.md`, the step's row in `docs/plan.md`, the architecture box it belongs to (`docs/architecture.md`), the related decisions (`docs/decisions.md`) and option cards (`docs/option-evaluation.md`), and the walkthroughs of earlier steps in `docs/walkthrough/`.

## 2. Plan, then stop
Write a short plan and **wait for approval**:
- what the step does, in two sentences, and where it sits in the flow (input → output)
- files to add or change, and the port(s) involved
- the option built now (the simplest that works: in-memory, rules, no model) and the options left for later
- the tests, including failure cases and any gate (permissions, deletes, publication)
- the demo command and what it will show
- **open questions**: anything ambiguous, any choice not settled in the docs, and any assumption you would otherwise make silently. Ask them here, numbered, with your suggested answer for each.

If a new question comes up while building, stop and ask; don't guess.

## 3. Build
- Code goes in the folder of the layer it belongs to; concrete options are wired only in `src/ekb/app/`.
- Small, readable modules with docstrings that explain **why**. Prefer clear code over clever code.
- Tests in the mirrored path under `tests/`.
- A demo: `uv run ekb demo <step>`, a subcommand that runs this step on the synthetic data and prints what happens in plain language. No Docker or paid model unless the step is about them.

## 4. Check
`uv run ruff format . && uv run ruff check . && uv run mypy && uv run lint-imports && uv run pytest -q`, and run the demo. Fix what fails.

## 5. Explain
Write `docs/walkthrough/S<nn>-<topic>.md` from `docs/walkthrough/_template.md`:
- how the data flows through this step
- the files built
- the main code path with short excerpts
- the options available
- what the tests prove
- how to run the demo
- simplifications and limits
- three "check your understanding" questions with answers

Add the file to the table in `docs/walkthrough/README.md`.

## 6. Hand over
- Set the step's status in `docs/plan.md` to 🟡 (built, awaiting review).
- Give the user a short summary: what to read first, the demo command, and the three questions.
- **Do not commit, push, or start the next step.** Codex reviews next (`$cross-review`).
