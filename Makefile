# Every target is a thin wrapper over a plain command, so nothing here is
# required: on Windows without make, run the command after the colon directly.
.DEFAULT_GOAL := help
.PHONY: help setup fmt lint typecheck imports test test-int check audit build up up-models up-app down logs options clean ui-types ui-check

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

setup: ## Install dependencies and git hooks
	uv sync --locked
	uv run pre-commit install

fmt: ## Format code
	uv run ruff format .
	uv run ruff check --fix .

lint: ## Lint (no changes)
	uv run ruff format --check .
	uv run ruff check .

typecheck: ## Static types (mypy strict)
	uv run mypy

imports: ## Enforce the folder/layer rules
	uv run lint-imports

test: ## Unit and contract tests with coverage
	uv run pytest -m "not integration and not e2e and not eval" --cov

test-int: ## Integration tests and gates on the real infrastructure (needs `make up`)
	uv run pytest -m integration

check: lint typecheck imports test ## Everything CI runs on a pull request, locally (UI: make ui-check)

audit: ## Known-vulnerability scan of locked dependencies
	uv run pip-audit --skip-editable

build: ## Build the service image
	docker build -f deploy/docker/Dockerfile -t ekb:local .

up: ## Start the infrastructure core
	docker compose up -d --wait

up-models: ## Core + Ollama
	docker compose --profile models up -d --wait

up-app: ## Core + mock sources + our three services (connector, worker, api; S17)
	docker compose --profile app up -d --build --wait

down: ## Stop everything (keeps volumes)
	docker compose --profile models --profile sources --profile app down

logs: ## Follow logs
	docker compose logs -f --tail=100

options: ## Show the chosen option per step and whether it is built
	uv run ekb options

clean: ## Remove caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov

ui-types: ## Regenerate the inspector UI's API types from the API schema
	uv run ekb inspector --export-openapi ui/openapi.json
	cd ui && npm run gen:types

ui-check: ## What CI runs for the inspector UI
	cd ui && npm ci
	$(MAKE) ui-types
	git diff --exit-code -- ui/openapi.json ui/src/api/schema.d.ts
	cd ui && npm test && npm run lint && npm run typecheck && npm run build
