# Every target is a thin wrapper over a plain command, so nothing here is
# required: on Windows without make, run the command after the colon directly.
.DEFAULT_GOAL := help
.PHONY: help setup fmt lint typecheck imports test test-int check audit build up up-models up-app down logs options clean

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

test-int: ## Integration tests (needs `make up`)
	uv run pytest -m integration

check: lint typecheck imports test ## Everything CI runs on a pull request, locally

audit: ## Known-vulnerability scan of locked dependencies
	uv run pip-audit --skip-editable

build: ## Build the service image
	docker build -f deploy/docker/Dockerfile -t ekb:local .

up: ## Start the infrastructure core
	docker compose up -d --wait

up-models: ## Core + Ollama
	docker compose --profile models up -d --wait

up-app: ## Core + our services
	docker compose --profile app up -d --build --wait

down: ## Stop everything (keeps volumes)
	docker compose --profile models --profile app down

logs: ## Follow logs
	docker compose logs -f --tail=100

options: ## Show the chosen option per step and whether it is built
	uv run ekb options

clean: ## Remove caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov
