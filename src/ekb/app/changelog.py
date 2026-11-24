"""Box 3 wired for the demos and the inspector: one bus, one registry, one raw store.

The connectors use the injection points introduced in S2:

==================  ===============================  ==========================
connector needs      given                            S2 had
==================  ===============================  ==========================
``sink``             ``changelog.publish``            a list
``store_original``   ``changelog.store_original``     a dict of ``mem://`` bytes
``known``            ``changelog.known(source)``      a dict rebuilt from events
==================  ===============================  ==========================

:meth:`ChangeLog.record_processed` is S3's stand-in worker: consumer group
``registry`` records every event as processed. Since S4, T2 does that job
(``ekb.app.diff``, group ``t2_diff``), deciding first whether to skip,
fast-track or purge; only ``ekb demo s3`` still uses the stand-in, so its replay
can read every original (T2 purges a deleted page's). The in-memory options are
chosen here by hand; choosing them in config comes with the pipeline wiring (S12).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock

from ekb.changelog.bus.consume import drain
from ekb.changelog.bus.options.in_memory import InMemoryEventBus
from ekb.changelog.bus.port import Delivery, EventBus
from ekb.changelog.rawstore.options.in_memory import InMemoryRawStore
from ekb.changelog.rawstore.port import RawStore
from ekb.changelog.registry.options.in_memory import InMemoryRegistry
from ekb.changelog.registry.port import DocumentRegistry, record_event
from ekb.contracts import ChangeEvent, ChangeOp
from ekb.contracts.listing import ListingVersion

REGISTRY_GROUP = "registry"
"""The stand-in worker's consumer group (``ekb demo s3`` only; T2 uses ``t2_diff``)."""


@dataclass(frozen=True)
class ChangeLog:
    bus: EventBus
    registry: DocumentRegistry
    rawstore: RawStore

    _lifetimes: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def current_lifetime(self, document_key: str) -> int:
        """The lifetime open for this key, even while consumers are catching up."""
        with self._lock:
            return self._lifetimes.get(document_key, 0)

    def store_original(self, document_key: str, version: str, original: bytes) -> str:
        with self._lock:
            lifetime = self._lifetimes.get(document_key, 0)
            return self.rawstore.put(document_key, version, original, lifetime=lifetime)

    def publish(self, event: ChangeEvent) -> Delivery:
        """Close the lifetime only after the bus accepts the delete."""
        key = event.document_key
        with self._lock:
            lifetime = self._lifetimes.get(key, 0)
            if event.op is ChangeOp.UPSERT and event.raw_pointer != self.rawstore.pointer_for(
                key, event.version, lifetime=lifetime
            ):
                raise ValueError(f"{key} original belongs to a closed lifetime")
            delivery = self.bus.publish(event.model_copy(update={"original_lifetime": lifetime}))
            if event.op is ChangeOp.DELETE:
                self._lifetimes[key] = lifetime + 1
            return delivery

    @classmethod
    def in_memory(cls, partitions: int = 3) -> ChangeLog:
        return cls(InMemoryEventBus(partitions), InMemoryRegistry(), InMemoryRawStore())

    def known(self, source: str) -> Callable[[], dict[str, ListingVersion]]:
        """What a full listing compares ``source`` against: the registry's live items."""
        return lambda: self.registry.known(source)

    def record_processed(self) -> int:
        """Stand-in worker: record every pending event in the registry; return how many."""
        return drain(self.bus, REGISTRY_GROUP, lambda d: record_event(self.registry, d.event))
