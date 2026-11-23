## What and why

<!-- One or two sentences. Link the plan task (docs/plan.md) or decision (docs/decisions.md). -->

## Layer and step

<!-- e.g. "4 · Process / T7 embed — new option `cohere_embed_v4`" -->

## Evidence

- [ ] `make check` passes locally
- [ ] New option? It implements the step's port and is covered by `tests/contract/`
- [ ] Changes a default choice in `config/`? Link the experiment result in `evaluation/results/`
- [ ] Touches permissions, deletes or publication? Gate tests updated (leakage = 0, deleted content = 0)

## AI-SDLC

- Written with: <!-- OpenCode + model / Copilot / Claude / by hand -->
- Reviewed by a different tool or person: <!-- which -->
