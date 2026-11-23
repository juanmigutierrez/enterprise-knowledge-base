---
name: cross-review
description: Review the current branch against main as an independent reviewer, read-only. Use after code was written by another AI tool or model, before committing or merging.
---

# Cross-review

You did not write this code. Assume nothing is correct until checked. **Do not edit files.**

1. Get the change: `git diff main...HEAD --stat` and `git diff main...HEAD` (plus `git diff` for uncommitted work).
2. Run `uv run lint-imports` and `uv run pytest -q`.
3. Check, in this order:
   1. **Gates.** Can a chunk become searchable before T8 sets its ACL and sensitivity? Can deleted content be returned? Can a user see a document their ACL does not allow? Any of these is a blocker.
   2. **Layer rules.** Does a step import another step, or does code outside `src/ekb/app/` import a concrete option?
   3. **Ports and tests.** Does each new option implement its port, appear in `tests/contract/`, and have unit tests with failure cases?
   4. **Model calls.** Any direct model SDK call instead of the gateway? Missing cache or cost logging?
   5. **Secrets and config.** Secrets in code, config or tests? A default in `config/` changed without a result in `evaluation/results/`?
   6. **Readability.** Would a first-time reader understand why the code exists?
4. Report a numbered list: `file:line`, severity (blocker / should-fix / nit), one-line fix.
5. End with `Verdict: approve` or `Verdict: changes requested`, the commands you ran, and a line for the commit trailer: `AI-Reviewed-by: <your tool and model>`.
