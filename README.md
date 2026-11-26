# Enterprise knowledge base for AI agents

This proof of concept lets an HR assistant answer questions with citations from sources the caller can read. Connectors watch synthetic HR documents, records and identity changes. A worker indexes passages with their access rules. When the assistant asks over MCP (Model Context Protocol), the knowledge API filters candidates by the caller's groups and rechecks access at the source before returning evidence.

## Run the lifecycle demo

Start with Git, uv and Python 3.13 (uv can install the pinned Python series). Clone the repository and run the following commands from a terminal. Package installation needs internet access or a populated uv cache.

```sh
git clone https://github.com/juanmigutierrez/enterprise-knowledge-base.git
cd enterprise-knowledge-base
```

If you are reviewing a branch, check it out now so the code matches the README you are reading. From the repository root, install the locked dependencies and run the demo.

```sh
uv sync --locked
uv run ekb demo lifecycle
```

The demo starts the mocks on loopback, uses in-memory stores and issues synthetic user tokens. It runs without Docker, model downloads or API keys.

Follow the mentoring policy through three requests. After an edit, Alice sees the updated meeting frequency once the worker runs. When her access is revoked, her next request loses that citation even before the worker runs. Later, deleting the travel policy removes its citation, including from a previously cached answer. At the end, look for zero restricted-page leaks, zero citations after deletion, and `promises held: 16 of 16`. A broken promise gives the command a nonzero exit status. [Step-by-step demo output](docs/walkthrough/S16-lifecycle.md)

## Explore the system

| To investigate | Starting point |
|---|---|
| How a change becomes a searchable passage | [Architecture and data flow](docs/architecture.md) and [walkthroughs in build order](docs/walkthrough/README.md) |
| Which implementations a profile selects | Run `uv run ekb options`. The lifecycle demo prints its active offline choices. |
| How the assistant asks questions | [MCP tools, caller identity and cited answers](docs/walkthrough/S15-mcp-hr-assistant.md) |
| How the same story behaves on Kafka, DynamoDB Local, SeaweedFS and OpenSearch | Start Docker, run `docker compose up -d --wait`, then `uv run ekb demo infra`. [Docker services and teardown](docs/walkthrough/S17d-services.md) |
| How retrieval quality and stale permissions were measured | [Evaluation results and experiment commands](docs/walkthrough/S18-evaluation.md) |
| How the project was built and checked | [AI development and review evidence](docs/ai-sdlc.md) and [clean setup check](docs/walkthrough/S19-clean-setup.md) |

## Assumptions and limits

The sources, people, access groups and policies are synthetic. In the quick start the worker uses offline model substitutes and the assistant quotes evidence. This makes access and deletion checks repeatable. The multilingual retrieval measurements use a separate embedding experiment. The local setup uses demo identities and loopback services. The production design covers AWS infrastructure and authentication separately. [Design decisions](docs/decisions.md)

## Development checks

```sh
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run lint-imports
uv run pytest -q --cov
```

The default pytest selection includes the permission, publication and deletion gates. Docker integration tests run separately with `uv run pytest -m integration` after starting the infrastructure. [Repository layout and developer workflow](docs/repo-structure.md)
