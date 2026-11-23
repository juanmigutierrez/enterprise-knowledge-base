# 1 · Sources (mocked)

Stand-ins for the department systems. They are **not** part of the product and
are never imported by `src/ekb`; the product only talks to them over HTTP, SQL
or the identity provider, exactly as it would to the real systems.

| Folder | Replaces | Behaviour that matters for the tests |
|---|---|---|
| `mock_docs/` ✅ S1 | SharePoint, Confluence | Pages with ACLs and a change feed for every create, edit, delete and permission change (webhook push in S2). Run: `uv run python -m sources.mock_docs` (port 8081) |
| `mock_hr_api/` ✅ S1b | Workday, Salesforce | REST with an inclusive `updated_since` filter and **no delete events**: deletes are only found by a full listing. Run: `uv run python -m sources.mock_hr_api` (port 8082) |
| `mock_idp/` ✅ S1c | Okta / Entra ID | Users and groups served SCIM-style, with joiner, leaver (deactivation) and group-membership events. Run: `uv run python -m sources.mock_idp` (port 8083). Keycloak is an optional Docker option in S17 |
| `mock_hr_db/` ✅ S1d | Operational databases | SQLite tables with a CDC-style change log written by triggers (snapshot, inserts, updates, deletes). Run: `uv run python -m sources.mock_hr_db init --path data/hr.sqlite`. Postgres + Debezium is an optional Docker option in S17 |
| `synthetic_data/` ✅ S1–S1d | — | One synthetic company seen by all four mocks: 12 people, 6 groups, 12 HR documents, HR records, database rows. See its README |
| `common/` | — | Shared plumbing: app factory, change feed, seed loaders, clock, directory. Each mock keeps only its own models and rules |

Each mock gets its own small Dockerfile and a service under the `sources` profile in `compose.yaml` when the Docker step (S17) needs it; until then they run as local processes.
`src/ekb` never imports this folder: an import-linter contract in `pyproject.toml` enforces it.
All data is synthetic.
