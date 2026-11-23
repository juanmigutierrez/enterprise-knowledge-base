---
name: plan-task
description: Implement one task from section 4.2 of docs/plan.md in this repository, plan first, then code, tests and checks. Use when asked to do "task N" or to implement a plan item.
---

# Implement a plan task

The user gives a task number from section 4.2 of `docs/plan.md`.

1. Read `AGENTS.md`, `docs/repo-structure.md`, the task row in `docs/plan.md`, and the decisions (`docs/decisions.md`) and option cards (`docs/option-evaluation.md`) the task depends on.
2. **Write a short plan and stop for approval.** List the files to add or change, the port(s) involved, the tests, and anything you are unsure about.
3. After approval, implement in the folder the layer belongs to. Concrete options are wired only in `src/ekb/app/`. Add or update tests in the mirrored path under `tests/`.
4. Run the checks and fix what fails:
   `uv run ruff format . && uv run ruff check . && uv run mypy && uv run lint-imports && uv run pytest -q`
5. Update the task's status in `docs/plan.md`.
6. Propose a commit message in conventional format (`feat(<scope>): ...`) ending with the trailers from `.gitmessage`, filled in with your tool and model. Leave `AI-Reviewed-by` for the reviewer.
