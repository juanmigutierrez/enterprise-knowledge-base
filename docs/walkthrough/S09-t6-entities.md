# S09 · T6 Entities: how documents get linked to people and teams

> Architecture: `docs/architecture.md` (box 4, "T6 · Extract + resolve entities"; box 5, "Entity store"; transformations table: "Chunk → canonical entity IDs"). Plan: `docs/plan.md` §4.2, S9. Option cards: `docs/option-evaluation.md` 4.10 (entity extraction: "cascade (exact match on HR data → Haiku → Sonnet when unsure)"; "precision matters more than recall") and 4.17 (entity store vs graph). Decisions: D3 (one canonical person ID), D6 (phase 1: an entity store, entity IDs written onto each chunk).

## 1. In one paragraph
A search for "the People team" should find every page that mentions it, whatever words the page uses around it. So T6 writes onto each chunk the **canonical IDs** of the people and teams it names: `team:T-PPL`, `person:bob`. It has two halves. **Resolve:** the HR record API (S1b) says who the company's people and teams are. Its records already flow through the change log, so T2 sends each employee and team record to the **entity store**: a new hire appears there, and a leaver's purge removes them. **Link:** right after T5, each chunk's words are matched, exactly, against those entities. A person is found by full name or work email, ignoring case and accents. A team is found only next to a word that says "team" ("People team", "Head of People", "equipo de People"), because "People", "Sales" and "Product" are also ordinary words. Precision comes first: a wrong link files a page under the wrong person. This is the first tier of card 4.10's cascade; the model tiers come later.

## 2. Where it sits in the flow
```
documents:  T2 ── process ──▶ T3 ──▶ T4 ──▶ T5 ──▶ [ T6 link ] ──▶ T7 … T8
                                                     1. directory.entities()   (the store, read only)
                                                     2. linker.link(parsed, chunks, entities)
                                                     3. linked(): only known IDs, sorted, once each

records:    T2 ── process ──▶ T3: "not a document" ──▶ [ T6 resolve: EntitySync.handle ]  ──▶ entity store
            T2 ── purge ─────────────────────────────▶ [ EntitySync.forget(record key) ]  ──▶ entity removed

input:  Chunk(…S6 and S8 fields…, entity_ids=(), acl=None, vector=None)
        Entity(entity_id, kind, name, aliases, team_id, source_key, source_version)   ← new in S9
output: Chunk(…, entity_ids=("person:bob", "team:T-PPL"), linker_id="exact_match@2") ← T6
        plus, per chunk, the mentions: EntityMention(entity_id, text, rule)          ← for the demo and UI
```
T6 runs only where T2 decided `process`, like T3 to T5. A `skip` (same text) and an `acl_only` (only the readers changed) never link again: the links come from the text, and the text didn't change.

## 3. What was built
| File | What it contains |
|---|---|
| `src/ekb/contracts/entities.py`, `__init__.py` | `EntityKind`, `Entity` (its ID starts with its kind; only a person has a `team_id`), `EntityMention`, `entity_id()` |
| `src/ekb/contracts/knowledge.py` | `Chunk.entity_ids` documented; `Chunk.linker_id` added |
| `src/ekb/stores/entities/port.py`, `options/in_memory.py` | `EntityStore` (`put`, `remove(source_key)`, `get`, `entities(kind)`); the `in_memory` option |
| `src/ekb/process/t6_entities/port.py` | `EntityLinker`, `EntityDirectory` (read only), `Linking`, `words(chunk)` |
| `src/ekb/process/t6_entities/match.py` | The exact-match rules: folding, people by name and email, teams with a team word, longest match, ambiguity |
| `src/ekb/process/t6_entities/options/exact_match.py` | `t6_entities: exact_match` |
| `src/ekb/process/t6_entities/records.py` | `hr_record`: an HR employee or team record → its `Entity`, or none |
| `src/ekb/process/t6_entities/sync.py` | `EntitySync`: puts a record's entity, forgets a purged record's |
| `src/ekb/process/t6_entities/step.py` | `LinkStep`, `LinkResult`, `check_input()`, `linked()`, `summary()` |
| `src/ekb/app/entities.py` | `entity_store()`, `linker()`, `link_step()`, `entity_sync()`, `RECORD_SOURCES` |
| `src/ekb/app/chunk.py` | `chunk_after_t3(t4, keep, t5, t6, records)`; `Processed.links`, `Processed.record`, `final` |
| `src/ekb/app/demos/t6.py`, `demos/__init__.py` | `ekb demo t6` |
| `src/ekb/app/inspector.py`, `operate/inspector/{entities,log,models,app}.py` | The live loop fills the store and runs T6; `EntityPanel`, `EntityLifetimes`; `GET /api/t6/entities`; links on the chunk views |
| `ui/src/components/EntitiesScreen.tsx`, `entities.ts`, `ChunkScreen.tsx`, `App.tsx`, `styles.css`; `ui/openapi.json`, `src/api/{schema.d.ts,client.ts}` | The **T6 Entities** tab; entity chips on the **T4 Chunk** tab; the regenerated contract |
| `tests/unit/process/t6_entities/*`, `tests/unit/stores/entities/test_in_memory.py` | The rules, the option, the records, the sync, the step, the store |
| `tests/unit/app/test_entities.py`, `test_demos.py`, `test_inspector_command.py` | The wiring behind T2, the demo, the live loop |
| `tests/contract/test_ports.py`, `tests/unit/test_contracts.py`, `tests/unit/operate/inspector/test_inspector.py`, `ui/tests/entities.test.mjs` | The two new ports, the contract, the API views and their deletion gate, the UI helpers |

## 4. How it works

### Resolve: from an HR record to an entity
The HR record API's connector (S2a) stores each record as canonical JSON and maps each employee to their **identity user** by full name (`PersonMap`, D3); the event carries it as `person_id`. `hr_record(event, original)` reads the stored record:

| record | entity |
|---|---|
| `employees/E-1002` (Bruno Moretti) | `person:bob`: name, work email as an alias, `team_id` `team:T-PPL` |
| `teams/T-PPL` (People) | `team:T-PPL`: name |
| an employee with no identity user | **none** |

A person's ID is the identity user's (`person:bob`), never the HR ID (`E-1002`): the ACLs name `bob`, so "pages about Bruno" and "pages Bruno may read" are about one person. Teams exist only in HR, so their ID is the HR team ID. An employee the connector could not map gets **no** entity: making one up from `E-1013` would give one person two IDs. That is fail closed, as the connectors do.

`EntitySync.handle(event)` runs where T2 decided `process` for a record (T3 reports "not a document"). It reads the original the event points to (the version the event names, as T3 does for a page) and puts the entity:
```python
entity = reader(event, self._rawstore.get(event.raw_pointer))
if entity is None:
    replaced = self._store.remove(event.document_key)  # no longer maps: drop what it defined
    ...
replaced = self._store.put(entity)  # one record defines at most one entity
```
`forget(document_key)` runs on a purge and removes the entity that record defined. The HR delta never reports a delete (S1b), so a leaver goes when the **full listing** finds the record missing.

### The entity store
`EntityStore` holds entities by ID, each remembering its record (`source_key`). `put` replaces whatever the same record defined before, so a record whose person mapping changed leaves no stale entity behind; `remove` takes the record's key, which is all a delete event carries. The store does **not** keep which documents mention an entity. Those links travel on the chunks into the search index (D6): they are permission-filtered with the chunk and go when the chunk goes, without a second copy to keep in step.

### Link: the exact-match rules
`ExactMatchLinker.link(document, chunks, entities)` builds the rules (`match.Index`) from the entities known right now, then reads each chunk's **section trail and text** (`words(chunk)`), never its context header: the header is a model's summary, and a link must point to words in the source.

| Rule | Matches | Doesn't match |
|---|---|---|
| `name`: a person's full name, case and accents ignored | "Bruno Moretti", "bruno moretti's", "Lucia Navarro" (Lucía), "Bruno⏎Moretti" | "Bruno", "Moretti", "Noah Martinez", "Noah Martin-Leroy" |
| `email`: a person's work email, case ignored | "daniela.ferri@example.com." | "daniela.ferri@example.com.au", "old.daniela.ferri@example.com" |
| `team`: the team's name **as HR writes it**, next to a team word | "People team", "Head of People", "equipo de People", "team People", "Finance⏎team" | "People who join…", "Engineering – Italy", "the people team", "compensation team" |

Case and accents are ignored by **folding** the text one character at a time, keeping where each character came from:
```python
folded = "".join(
    c for c in unicodedata.normalize("NFKD", char) if not unicodedata.combining(c)
).casefold()
...
out.extend(folded)
origin.extend([index] * len(folded))
```
So a match in the folded text is shown as the words the page wrote ("Lucia Navarro"). Team names are matched on the original text, so the capital letter counts. T6 matches each heading and the body separately. A name cannot begin in a heading and end in the body. A one-word HR name can still be linked by its work email. Two more rules keep precision:
- **The longest match wins.** Spans are sorted leftmost first, then longest, and a span inside one already taken is dropped. "Ana Ruiz Mora" is not also "Ana Ruiz".
- **A name two entities share links to neither.** It is reported in the note (`not linked, ambiguous: 'Marco Bianchi' names 2 entities (…)`), and it still blocks the shorter names inside it.

Each entity is reported once per chunk, at its first mention, with the words and the rule.

### The step checks, the linker only answers
`LinkStep.handle(document, chunks)` reads the entities from the directory, calls the linker, then `linked()`:
- exactly one tuple of mentions per chunk, or `LinkError`;
- every mentioned ID must be in the directory, so a linker can't invent an entity or link to one just removed;
- the IDs go onto the chunk **sorted, each once**, with `linker_id`; nothing else changes.

As in T5, chunks from another document version, or that already carry an ACL or a vector, are refused: T6 runs before T7 and T8.

### Wiring
`chunk_after_t3(t4, keep, t5, t6, records)` sends a record that defines an entity to `records`, and a document through T4, T5 and T6; only then does it call `keep`. If any step raises, nothing is kept, T2 records nothing and the event comes back (S4's rule). `forgetter(gateway, …, records.forget)` makes a purge drop a deleted record's entity too. In `ekb inspector`, round 0 runs the HR delta before the docs listing, so the store is full before the pages are linked.

### The inspector and the UI
`GET /api/t6/entities[?kind=person|team]` returns the store's entities, each with the live chunks that name it (document, chunk, words, rule), the counts, and the **stale links**: links to entities the store no longer holds. `GET /api/t4/chunks/document` gains `entity_ids`, `linker_id` and the mentions per chunk, and the list gains each document's entities and T6's note. An entity is hidden as soon as its record's delete is published, before T2's purge, as T3's and T4's output are (`EntityLifetimes`).

The **T6 Entities** tab lists the people and teams (filter by kind) with their link counts. Click one to see its record and every chunk that names it, with the words. A box below lists the stale links. On the **T4 Chunk** tab each chunk shows a chip per linked entity (hover: the words and the rule), and the list has an "entities" column.

## 5. Options
Built now, chosen by hand in `ekb.app.entities` (choosing from config comes with the pipeline, S12):
- `t6_entities: exact_match`: the `ci` profile's choice, and what the demo and the inspector use.
- `entities: in_memory`: the store for the demo, the inspector and the tests.

`config/default.yaml` still names `t6_entities: cascade` and `entities: dynamodb`, the starting choices of cards 4.10 and 4.17; `ekb options` lists both as `not built yet`, with `exact_match` and `in_memory` available.

Later, each one more file behind the same ports:
- **`model`**: one call per document through the gateway's `entities` task, for mentions the rules leave ("the Head of People's approval" → Bruno, a bare "Engineering"), returning only IDs the directory holds;
- **`cascade`**: exact match first, then the light model, then `entities_hard` for conflicts or low confidence (card 4.10, measured on 50 hand-labelled chunks in S18);
- **`dynamodb`** for the store (S17), and **Neo4j / Neptune** for relations in phase 2 (D6, card 4.17);
- more entity kinds (retailers, brands, campaigns) from the CRM sources, each with its own record reader.

## 6. What the tests prove
- **The rules:**
  - the synthetic records give 12 people and 5 teams, none ambiguous;
  - full names are found whatever the case, spacing, line break or possessive; accents are ignored both ways and the words are shown as written; work emails, but not inside a longer address;
  - a first name, a surname, both apart, a longer word or a hyphenated surname are not mentions;
  - a team is found next to a team word in EN, ES and IT; a bare name, a heading, lowercase "people team", an unknown "compensation team" and "Peoples team" are not;
  - each entity once, in text order; a shared name links to neither and is reported; the longest match wins, and an ambiguous longer name still blocks the shorter one.
- **The option on the seed pages:** `HR-PL-04` links the People team twice; `HR-CMP-01` links "Head of People" but not "Engineering – Italy"; `HR-PL-07` links "equipo de People"; no seed page names a person. A contacts page links names, an email and a team, not "Marco" alone. The section trail is read, the context header is not. The note says what was known and what was skipped; with no entities, nothing is linked and the note says why.
- **Records:** an employee becomes `person:<identity user>` with email and team; a team becomes `team:<HR id>`; an employee with no identity user is no entity; a broken record, a record whose ID differs from the event's, or an unknown collection raises `RecordError`.
- **The sync and the store:** a record puts its entity and a purge removes it (twice is harmless); a rename replaces it; a record that no longer maps drops its old entity; a broken record leaves the store as it was. The store keeps one entity per record, removes by record key, and leaves no stale entity when a record's entity ID changes.
- **The T6 step:** IDs go on the chunks and nothing else changes; links open no gate. IDs are sorted and kept once; an unknown ID, the wrong number of mention sets, chunks from another version, or chunks with an ACL or a vector are refused (`LinkError`). A removed entity is not linked by the next document.
- **Behind T2:** HR records fill the store, then a page is linked to them, after T5. A skip and the fast lane never link again. **Deletion gate:** a purged HR record removes its entity, and the page's next version no longer links to it; a purged page drops its chunks and leaves the entities alone. An unmapped employee is no one to link to. If T6 fails, nothing is kept and T2 records nothing.
- **The live loop:** round 0 fills the store (17) before linking the pages; `HR-PL-04` is linked with its words and note; deleting `E-1009` in the HR API removes `person:lucia` at the next full listing.
- **API and UI:** the chunk view carries the links and the words; the entity list shows each entity with its chunks, filtered by kind; a removed entity's link is listed as stale; **an entity whose record's delete is published is hidden before the purge**; a purged page takes its links off the entities. The contract is regenerated; the UI helpers are tested.
- **The demo:** the whole story runs, and the leaver leaves the store.

## 7. Run it yourself
```powershell
uv run ekb demo t6
```
No keys needed: T6 calls no model, and the demo writes no context header. You should see:
1. **Who the company knows:** `HR delta sync() -> 17 events`, then the 12 people (`person:bob  Bruno Moretti  bruno.moretti@example.com  team:T-PPL`) and the 5 teams. Bruno is `E-1002` in HR and `person:bob` here.
2. **The pages:** one line per page (`HR-PL-04   2 of 4 chunks linked: team:T-PPL x2`), then `6 of 48 chunks are linked; 0 of them to a person.` No seed page names a person.
3. **Every link, with the words behind it:** `hr_docs:HR-CMP-01#3  team:T-PPL  team  'Head of People'`, `HR-PL-07#2 … 'equipo de People'`, and four "People team".
4. **Near misses:** lowercase "people" (`HR-PL-10#2`), "your manager", "compensation team", a bare "Engineering".
5. **A contacts page:** dana creates `HR-PL-12`. Chunk 0 links `person:bob` ('Bruno Moretti'), `team:T-PPL` and `person:dana` (by her email); chunk 1 links `person:lucia` ('Lucia Navarro', no accent). "Marco" alone is not linked.
6. **A leaver:** bob deletes Lucía's HR record. The delta finds 0 events; the full listing finds the delete, T2 purges it, the entity goes. `HR-PL-12` **still** links her on `#1`: it was linked before she left and hasn't changed. dana edits the page; now `#1` has no links.
7. **A delete:** `HR-PL-12` is purged, with its chunks and their links.
8. **Summary:** `Entity store: 11 people, 5 teams. Linked chunks: 6 of 48. Publishable chunks: 0 of 48`.

In the inspector (`uv run ekb inspector --header none`, then `npm run dev` in `ui/`), open **T6 Entities**: 12 people and 5 teams, `team:T-PPL` with six links. To see a person linked, add a page naming one through the docs tool's `/docs` page (POST `/pages`, as in scene 5). Then delete that person's record in the HR API's `/docs` page and wait for the full listing (every 10 rounds by default): the person leaves the list, and their link moves to **Links to entities the store no longer holds**.

## 8. Decisions and limits
- **Settled in S9's plan (25 November 2026):**
  - entities come from HR record events through the change log, not from a snapshot of the API;
  - IDs are `person:<identity user>` and `team:<HR team ID>`;
  - people match by full name or email, teams only next to a team word, and nothing is linked from the context header;
  - links are not redone when an entity changes (below);
  - the seed stays at 12 pages, and the demo creates the contacts page;
  - the demo runs offline.
- **Links are written when a page is processed.** A page processed before a hire is not linked to them, and one processed before a leaver keeps their ID, until it changes or is rebuilt. The store no longer answers for a removed ID, so nothing about the leaver can be looked up through it, and the inspector lists such links as stale. Re-linking the pages an entity change affects is part of self-maintenance (S16). Order matters too: in the inspector and the demo the HR records load first; across real sources with separate partitions that order is not guaranteed, which is the same limit.
- **Exact match finds only what it is sure of.** Roles ("the Head of People's approval" as Bruno), bare team names, nicknames and misspellings are left to the model tiers. A person sharing a name with another is linked to neither.
- **The rules are rebuilt for every document** from the store (17 entities here). At thousands of entities, production keeps one compiled index (or a trie) and rebuilds it when the store changes.
- **People and teams only.** Offices ("the Milan office"), policies referenced by code ("see HR-PL-03") and retailers or brands are not entities yet; D6 lists them for later.
- **No relations beyond a person's team.** Who leads a team, who reports to whom, and valid-from / valid-to dates are phase 2 (D6).
- **Restricted pages are linked like any other.** A link is only as visible as its chunk: the search index filters both by the reader's permissions (S11, S13). A `get_entity` tool (S15) must filter "pages about X" the same way.
- **The demo and the inspector choose options by hand**, as earlier steps did; the pipeline (S12) reads them from config.

## 9. Check your understanding
1. `HR-PL-10` says "people" in a sentence and "the People team" a few lines later; `HR-CMP-01` has a heading "Engineering – Italy". Which of these become links, and why are the rules this strict when a model could guess more?
2. Lucía Navarro leaves. Why does the HR delta not notice, what finally removes `person:lucia` from the store, and what happens to `HR-PL-12`'s chunk that named her, before and after dana edits the page?
3. Why is Bruno's entity `person:bob` and not `person:E-1002`? What does T6 do with an HR employee who has no identity user, and why not just use their HR ID?

<details>
<summary>Answers</summary>

1. Only "the People team" (`team:T-PPL`, rule `team`). A team's name counts only next to a team word, written as HR writes it. "people" in a sentence is an ordinary word, and "Engineering – Italy" is a bare name with no team word, so neither is linked. Card 4.10 puts precision first: a wrong link files a page under the wrong person or team and pollutes the entity store for everyone, while a missed one can still be found later by the model tiers of the cascade.
2. The delta asks for records saved since the last poll; a deleted record is simply absent, so the delta stays silent (S1b). The HR **full listing** compares every record with the registry, finds `employees/E-1009` missing and emits a delete. T2 purges it, and the purge calls `EntitySync.forget`, which removes her entity. `HR-PL-12#1` was linked before she left and the page hasn't changed, so it keeps `person:lucia` (a stale link; the store no longer answers for it). When dana edits the page, T2 decides `process` and T6 links it again. "Lucia Navarro" is still on the page, but no entity has that name now, so `#1` has no links.
3. D3: the one canonical person ID is the identity provider's user ID. The ACLs, `person_id` and `changed_by` all use `bob`, so linking pages to `person:bob` keeps "pages about Bruno" and "pages Bruno may read" about the same person. An employee the connector could not map gets **no** entity, and that is reported. Using `E-1013` would create a second ID for one person: when the identity user appears later, the person would be split across two IDs and their links would not line up with their permissions.
</details>
