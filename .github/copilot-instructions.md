# Copilot instructions

The full rules for AI tools are in `AGENTS.md` at the repository root; follow them. The essentials, repeated here for Copilot code review (which reads only this file):

- Code goes in the layer folder that matches the architecture (`src/ekb/connect`, `changelog`, `process/t2..t8`, `stores`, `access/q1..q4`, `operate`, `evaluation`). Concrete options are imported only in `src/ekb/app/`. Steps never import each other; `uv run lint-imports` enforces this.
- Each step has a `port.py` Protocol; each option is one file in `options/` with `@register("<step>", "<name>")`, a contract test, and unit tests in the mirrored path under `tests/unit/`.
- Gates: no chunk is searchable before T8 sets its ACL and sensitivity; deleted or unauthorized content is never returned. Flag any change that weakens this as a blocker.
- Every model call goes through the platform model gateway. No secrets in code, config or tests.
- Python 3.13, mypy strict, ruff, pytest. Changing `config/default.yaml` needs a result file in `evaluation/results/`.
