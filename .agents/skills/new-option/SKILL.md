---
name: new-option
description: Add a new option (implementation) to a pipeline step, with registration, contract test and unit tests. Use when asked to add an option such as "add cohere_embed_v4 to t7_embed".
---

# Add an option to a step

Inputs: the step (for example `t7_embed`) and the option name (for example `cohere_embed_v4`).

1. Find the step's folder and its `port.py`. If the port does not exist yet, write it first from the step's docstring and its option card in `docs/option-evaluation.md`, and add it to `PORTS` in `tests/contract/test_ports.py`.
2. Create `options/<option>.py` with one class decorated `@register("<step>", "<option>")`. Parameters come from config; secrets only from environment variables. Model calls go through the platform model gateway.
3. Add unit tests in the mirrored path under `tests/unit/`, including failure cases. No network or paid models in unit tests.
4. If the option is a candidate in an experiment, add it to the matching file in `evaluation/experiments/`.
5. Run `uv run ruff format . && uv run ruff check . && uv run mypy && uv run lint-imports && uv run pytest -q` and fix what fails.
6. Do **not** change `config/default.yaml`; defaults change only with an experiment result.
