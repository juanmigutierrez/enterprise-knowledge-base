# 1 · Sources (mocked)

Stand-ins for the department systems. They are **not** part of the product and
are never imported by `src/ekb`; the product only talks to them over HTTP, SQL
or the identity provider, exactly as it would to the real systems.

| Folder (planned) | Replaces | Behaviour that matters for the tests |
|---|---|---|
| `mock_docs/` | SharePoint, Confluence | Pages with ACLs; a webhook on every create, edit, delete and permission change |
| `mock_hr_api/` | Salesforce, Workday | REST with an `updated_since` filter and no delete events (deletes found by listing) |
| `hr_db/` | Operational databases | Postgres with logical replication on, for Debezium |
| `keycloak/` | Okta / Entra ID | Realm export with test users, groups and roles |
| `synthetic_data/` | — | Generator and seed documents: several languages, one restricted document, one to delete |

Each mock gets its own small Dockerfile and a service under the `sources` profile in `compose.yaml`.
All data is synthetic.
