"""T6 wired behind T5 (S9), and the entity store kept current from HR records.

Three choices are made here, in the composition root, and nowhere else:

- **Which store and which linker.** By name: the worker pipeline (S12) passes
  what the profile names. ``config/default.yaml`` names ``cascade`` and
  ``dynamodb`` (card 4.10's and 4.17's starting choices). ``cascade`` runs as
  its stand-in, ``exact_match``, until it is built; ``dynamodb`` is built (S17)
  and runs with ``--docker``, ``in_memory`` otherwise.
- **Which sources define entities**, and how to read their records:
  ``RECORD_SOURCES``. Only the HR record API today: its employees and teams.
- **When each half runs.** A record T3 reports as "not a document" updates
  the store (``chunk_after_t3``'s ``records``); a purge removes what the
  record defined (add :meth:`EntitySync.forget` to the ``forgetter``); T6
  links a document's chunks right after T5.
"""

from __future__ import annotations

from typing import Any

from ekb.app.changelog import ChangeLog
from ekb.platform import registry
from ekb.process.t6_entities.options.exact_match import ExactMatchLinker
from ekb.process.t6_entities.port import EntityLinker
from ekb.process.t6_entities.records import RecordReader, hr_record
from ekb.process.t6_entities.step import LinkStep
from ekb.process.t6_entities.sync import EntitySync
from ekb.stores.entities.options.in_memory import InMemoryEntityStore
from ekb.stores.entities.port import EntityStore

LINKERS = (ExactMatchLinker,)  # imported, so it is registered
STORES = (InMemoryEntityStore,)
DEFAULT_LINKER = "exact_match"
LINKER_OPTIONS = ("exact_match",)
DEFAULT_STORE = "in_memory"

RECORD_SOURCES: dict[str, RecordReader] = {"hr_api": hr_record}
"""Sources whose records define entities, each with the reader for its records."""


def entity_store(name: str = DEFAULT_STORE, **params: Any) -> EntityStore:
    """A registered entity store option by name (``in_memory``, ``dynamodb``)."""
    built = registry.create("entities", name, **params)
    if not isinstance(built, EntityStore):
        raise TypeError(f"entities option {name!r} does not implement the EntityStore port")
    return built


def linker(name: str = DEFAULT_LINKER) -> EntityLinker:
    """A registered T6 option by name (``exact_match``)."""
    built = registry.create("t6_entities", name)
    if not isinstance(built, EntityLinker):
        raise TypeError(f"t6_entities option {name!r} does not implement the EntityLinker port")
    return built


def link_step(store: EntityStore, name: str = DEFAULT_LINKER) -> LinkStep:
    """T6 reading its entities from ``store``."""
    return LinkStep(linker(name), store)


def entity_sync(changelog: ChangeLog, store: EntityStore) -> EntitySync:
    """The entity store fed from the change log's stored records."""
    return EntitySync(store, changelog.rawstore, RECORD_SOURCES)
