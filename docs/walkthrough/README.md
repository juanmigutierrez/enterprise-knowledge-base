# Walkthroughs: one per build step

Each step in `docs/plan.md` §4.2 ends with a walkthrough here, so the system can be understood one piece at a time, in the order data flows through it.

Files are named `S<nn>-<topic>.md` (for example `S02-t1-normalize.md`) and follow `_template.md`.

| Step | Walkthrough |
|---|---|
| S0 | Foundations: see `docs/repo-structure.md` |
| S1 | [Sources: the mock HR docs service](S01-sources.md) |
| S1b | [Sources: the mock HR record API](S01b-hr-api.md) |
| S1c | [Sources: the mock identity provider](S01c-identity.md) |
| S1d | [Sources: the mock HR database](S01d-hr-db.md) |
| S2 | [Connect + T1: the docs connector and the one change event](S02-t1-normalize.md) |
| S2a | [Connect + T1 for every source type](S02a-connectors.md) |
| S2b | [Inspector UI foundation](S02b-inspector.md) |
| S3 | [Change log: event bus, document registry, raw store](S03-change-log.md) |
