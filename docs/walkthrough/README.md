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
| S4 | [T2 Diff: skip unchanged, ACL fast lane, purge deletes](S04-t2-diff.md) |
| S5 | [T3 Parse: from a stored original to clean, structured text](S05-t3-parse.md) |
| S6 | [T4 Chunk: from parsed blocks to the passages that will be searched](S06-t4-chunk.md) |
| S7 | [Model gateway: every model call routed, reused, guarded and costed](S07-model-gateway.md) |
| S8 | [T4 context header + T5 Classify: what metadata each chunk carries, and why](S08-t4-header-t5-classify.md) |
| S9 | [T6 Entities: how documents get linked to people and teams](S09-t6-entities.md) |
| S10 | [T7 Embed: what a vector is and why the model ID is stored](S10-t7-embed.md) |
| S11 | [T8 Permissions and the search store: why nothing is searchable before permissions are set](S11-t8-permissions.md) |
| S12 | [The worker pipeline: the full write path, T2 → T8 on the bus](S12-pipeline.md) |
