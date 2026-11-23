# AGENTS.md: rules for AI coding tools in this repository

Read by Codex, Claude Code (through `CLAUDE.md`, which imports this file), Copilot and any other agent. Humans: this is also the short version of `docs/repo-structure.md`.

## What this is
An enterprise knowledge base for AI agents. Sources change, connectors emit change events, workers turn them into permission-aware knowledge, and agents query it through MCP. Design: `docs/architecture.md`, `docs/decisions.md`. Plan and status: `docs/plan.md`.

## Where code goes
The folder layout mirrors the architecture diagram. Put code in the layer it belongs to:

| # | Layer | Folder |
|---|---|---|
| 1 | Sources (mocks) | `sources/` (never imported by `src/ekb`) |
| 2 | Connect + T1 | `src/ekb/connect/` |
| 3 | Change log | `src/ekb/changelog/{bus,registry,rawstore}/` |
| 4 | Process T2–T8 | `src/ekb/process/t<n>_<name>/` |
| 5 | Knowledge stores | `src/ekb/stores/<store>/` |
| 6 | Access Q1–Q4 + MCP | `src/ekb/access/` |
| 7 | Agents | `agents/` (outside the product) |
| 8 | Operate | `src/ekb/operate/` (runtime), `src/ekb/evaluation/` + `evaluation/` (quality) |

Shared models are in `src/ekb/contracts/`. Plumbing with no business logic (registry, config, model gateway) goes in `src/ekb/platform/`. Wiring happens only in `src/ekb/app/`.

## How a step is built
Each step or store has:
- `port.py`: a `typing.Protocol` that every option implements
- `options/<name>.py`: one class per option, decorated `@register("<step>", "<name>")`

To add an option, add one file in `options/`, add its port to `tests/contract/test_ports.py` if it is new, and add unit tests under `tests/unit/` in the same path. Never import a concrete option outside `src/ekb/app/` or tests. Never import one step from another step.

These rules are enforced: `uv run lint-imports` fails the build on violations.

## Hard rules
- Permissions and deletes are gates: no chunk is searchable before T8 has set its ACL and sensitivity, and deleted content must never be returned. Any change touching these needs a test.
- No secrets in code, config or tests. Configuration goes in `config/*.yaml`, secrets in environment variables.
- Every model call goes through the platform model gateway (content-hash cache, per-task model, cost log). Never call a model SDK directly.
- Changing a default in `config/default.yaml` requires an experiment result in `evaluation/results/`.
- Synthetic data only.

## Commands
```
uv sync --locked                 # install
uv run ruff format . && uv run ruff check --fix .
uv run mypy                      # strict
uv run lint-imports              # layer rules
uv run pytest -m "not integration and not e2e and not eval" --cov
docker compose up -d --wait      # infrastructure for integration tests
uv run ekb options               # chosen option per step and whether it is built
```
`make check` runs everything a pull request runs.

## Style
Python 3.13, type hints everywhere (mypy strict), Pydantic models for anything crossing a boundary, small modules, docstrings that explain why, not what. Conventional commit messages (`feat(t7_embed): add cohere option`).

## Review and traceability
- Fixed roles (decisions.md D17): **Claude Code writes** the code (`/plan-task <n>`). **Codex reviews, fixes and pushes** (`$cross-review`): it reports findings first, fixes blockers and should-fixes with a test for each, re-runs all checks, and commits and pushes only after the user confirms. Design decisions are never made in review; they go back to the user.
- Commit messages end with the trailers in `.gitmessage` (`AI-Tool`, `AI-Reviewed-by`, `Review-fixes`, `Human-changes`). The PR template asks the same.
- Process and evidence log: `docs/ai-sdlc.md`.

## Skills (shared by Codex and Claude Code)
Defined once in `.agents/skills/`; Claude Code reaches them through thin wrappers in `.claude/skills/`.
- `plan-task`: implement a task from `docs/plan.md` (plan first, then code, then checks)
- `new-option`: add an option to a step with its registration and tests
- `cross-review`: review the branch against `main`, fix what it finds, verify, then commit and push after confirmation

Invoke in Codex with `$plan-task 4` (or `/skills`), in Claude Code with `/plan-task 4`.
