"""T6 · Extract and resolve entities (layer 4, process). Card 4.10.

T6 links every chunk to the people and teams it names, by their canonical
entity IDs (``person:bob``, ``team:T-PPL``), so the access layer can later find
"everything about the People team" and the entity store can say who they are.

It has two halves:

- **resolve** (``records.py``, ``sync.py``): the HR record API's employees and
  teams become the canonical entities in the entity store, kept current from
  their change events: a new hire appears, a leaver's purge removes them;
- **link** (``port.py``, ``options/``, ``step.py``): each chunk's words are
  matched against those entities and the IDs written onto the chunk.

Built in S9: the ``EntityLinker`` port and the ``exact_match`` option, the
first tier of card 4.10's cascade. ``model`` and ``cascade`` (exact match, then
a light model, then a strong one for hard cases) come later, through the
gateway's ``entities`` and ``entities_hard`` tasks.
"""
