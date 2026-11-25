"""What the inspector shows of the entity store (S9): the people and teams T6 links to.

The panel reads the same store the live loop's T6 reads, so the **T6 Entities**
tab shows exactly the entities a page is matched against right now. An entity
is shown only while the record that defined it is alive: a leaver's record,
once its delete is published, hides the entity even before T2's purge removes
it (:class:`~ekb.operate.inspector.log.EntityLifetimes`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ekb.contracts import Entity, EntityKind
from ekb.operate.inspector.log import EntityLifetimes
from ekb.stores.entities.port import EntityStore


@dataclass(frozen=True)
class EntityPanel:
    store: EntityStore
    lifetimes: EntityLifetimes = field(default_factory=EntityLifetimes)

    def visible(
        self, current_lifetime: Callable[[str], int], kind: EntityKind | None = None
    ) -> tuple[Entity, ...]:
        """The entities whose record is still the one they were read from."""
        # The writer puts the entity before recording its lifetime. Read in
        # reverse order so a recreated record cannot validate an older entity.
        lifetimes = self.lifetimes.snapshot()
        return tuple(
            entity
            for entity in self.store.entities(kind)
            if lifetimes.get(entity.source_key) == current_lifetime(entity.source_key)
        )
