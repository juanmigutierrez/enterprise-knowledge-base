"""Box 3 wired for the demos and the inspector: one bus, one registry, one raw store.

The connectors use the injection points introduced in S2:

==================  ===============================  ==========================
connector needs      given                            S2 had
==================  ===============================  ==========================
``sink``             ``changelog.bus.publish``        a list
``store_original``   ``changelog.rawstore.put``       a dict of ``mem://`` bytes
``known``            ``changelog.known(source)``      a dict rebuilt from events
==================  ===============================  ==========================

Until the worker pipeline exists, :meth:`ChangeLog.record_processed` is the
stand-in worker: consumer group ``registry`` records every event as processed.
T2 takes that over in S4 (it will skip, fast-track or purge before recording).
The in-memory options are chosen here by hand; choosing them in config comes
with the pipeline wiring (S12).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ekb.changelog.bus.consume import drain
from ekb.changelog.bus.options.in_memory import InMemoryEventBus
from ekb.changelog.bus.port import EventBus
from ekb.changelog.rawstore.options.in_memory import InMemoryRawStore
from ekb.changelog.rawstore.port import RawStore
from ekb.changelog.registry.options.in_memory import InMemoryRegistry
from ekb.changelog.registry.port import DocumentRegistry, record_event
from ekb.contracts.listing import ListingVersion

REGISTRY_GROUP = "registry"
"""The stand-in worker's consumer group (T2's from S4)."""


@dataclass(frozen=True)
class ChangeLog:
    bus: EventBus
    registry: DocumentRegistry
    rawstore: RawStore

    @classmethod
    def in_memory(cls, partitions: int = 3) -> ChangeLog:
        return cls(InMemoryEventBus(partitions), InMemoryRegistry(), InMemoryRawStore())

    def known(self, source: str) -> Callable[[], dict[str, ListingVersion]]:
        """What a full listing compares ``source`` against: the registry's live items."""
        return lambda: self.registry.known(source)

    def record_processed(self) -> int:
        """Stand-in worker: record every pending event in the registry; return how many."""
        return drain(self.bus, REGISTRY_GROUP, lambda d: record_event(self.registry, d.event))
