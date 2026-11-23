---
name: cross-review
description: Review the branch written by Claude Code against main, fix the problems found, re-run the checks, then commit and push after the user confirms. Use when asked to review, cross-review, or finish a task before pushing.
---

# Cross-review, fix and push

You are the reviewer. Claude Code wrote this code; assume nothing is correct until checked.

## 1. Review (no edits yet)
1. Get the change: `git diff main...HEAD --stat`, `git diff main...HEAD`, and `git diff` for uncommitted work.
2. Run `uv run lint-imports` and `uv run pytest -q`.
3. Check, in this order:
   1. **Gates.** Can a chunk become searchable before T8 sets its ACL and sensitivity? Can deleted content be returned? Can a user see a document their ACL does not allow? Any of these is a blocker.
   2. **Layer rules.** Does a step import another step, or does code outside `src/ekb/app/` import a concrete option?
   3. **Ports and tests.** Does each new option implement its port, appear in `tests/contract/`, and have unit tests with failure cases?
   4. **Model calls.** Any direct model SDK call instead of the gateway? Missing cache or cost logging?
   5. **Secrets and config.** Secrets in code, config or tests? A default in `config/` changed without a result in `evaluation/results/`?
   6. **Readability.** Would a first-time reader understand why the code exists?
4. Report a numbered list: `file:line`, severity (blocker / should-fix / nit), one-line fix.

## 2. Fix
- Fix every **blocker** and **should-fix**. Fix a nit only if it is a one-line change.
- Keep fixes minimal and inside the task's scope: no refactoring, no new features, no changes to `config/default.yaml`.
- Add a test for every bug you fix.
- If a fix needs a design decision (for example changing a port or a contract), do not make it: list it under "Needs a decision" and stop.

## 3. Verify
Run `uv run ruff format . && uv run ruff check . && uv run mypy && uv run lint-imports && uv run pytest -q`. Everything must pass.

## 4. Summarize and wait
Show:
- the findings, each marked **fixed**, **not fixed (why)** or **needs a decision**
- `git diff --stat` of your fixes
- `Verdict: approve` (nothing left open) or `Verdict: changes requested`

Then **ask the user to confirm** before committing.

## 5. Commit and push (only after the user confirms)
1. Update the task's status in `docs/plan.md` and add a row to the evidence log in `docs/ai-sdlc.md`.
2. Commit with a conventional message and these trailers:
   ```
   AI-Tool: Claude Code + <model>
   AI-Reviewed-by: Codex + <your model>
   Review-fixes: <n> fixed by Codex: <short list>, or "none"
   Human-changes: <ask the user, or "none">
   ```
3. Merge into `main` and push: `git switch main && git merge --no-ff <branch> && git push`.
