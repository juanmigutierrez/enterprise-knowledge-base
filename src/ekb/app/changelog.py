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
can read every original (T2 purges a deleted page's). :meth:`ChangeLog.in_memory`
is the step demos' hand-picked change log; the worker pipeline builds the one the
profile names (``ekb.app.pipeline.build_changelog``, S12).

**What the bus accepted** lives in the acceptance log (S17,
``ekb.changelog.acceptance``): each item's open lifetime, its latest delivery and
its accepted record. With the in-memory option the change log works as before;
with ``dynamodb`` every process (connectors, worker, Knowledge API) sees the same
lifetimes, which is what lets them run as separate services.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock

from ekb.changelog.acceptance.options.in_memory import InMemoryAcceptanceLog
from ekb.changelog.acceptance.port import AcceptanceLog
from ekb.changelog.bus.consume import drain
from ekb.changelog.bus.options.in_memory import InMemoryEventBus
from ekb.changelog.bus.port import Delivery, EventBus
from ekb.changelog.rawstore.options.in_memory import InMemoryRawStore
from ekb.changelog.rawstore.port import RawStore
from ekb.changelog.registry.options.in_memory import InMemoryRegistry
from ekb.changelog.registry.port import DocumentRegistry, known_versions, record_event
from ekb.contracts import ChangeEvent, ChangeOp
from ekb.contracts.listing import ListingVersion

REGISTRY_GROUP = "registry"
"""The stand-in worker's consumer group (``ekb demo s3`` only; T2 uses ``t2_diff``)."""


@dataclass(frozen=True)
class ChangeLog:
    bus: EventBus
    registry: DocumentRegistry
    rawstore: RawStore
    acceptance: AcceptanceLog = field(default_factory=InMemoryAcceptanceLog)
    """What the bus accepted, per item: lifetimes and latest deliveries (S17: shared by
    every process when it is the ``dynamodb`` option)."""

    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def current_lifetime(self, document_key: str) -> int:
        """The lifetime open for this key, even while consumers are catching up."""
        entry = self.acceptance.get(document_key)
        return entry.lifetime if entry is not None else 0

    def latest_delivery(self, delivery: Delivery) -> bool:
        """The worker has caught up unless the connector recorded a later event.

        Kafka can deliver before the connector finishes its acceptance write.
        All deliveries for this key are processed in order on the same partition.
        """
        entry = self.acceptance.get(delivery.event.document_key)
        return entry is None or (
            entry.partition == delivery.partition and entry.offset <= delivery.offset
        )

    def store_original(self, document_key: str, version: str, original: bytes) -> str:
        with self._lock:
            lifetime = self.current_lifetime(document_key)
            return self.rawstore.put(document_key, version, original, lifetime=lifetime)

    def publish(self, event: ChangeEvent) -> Delivery:
        """Publish first, then record the event as accepted: a delete closes its
        lifetime only once the bus holds it (``ekb.changelog.acceptance.port``)."""
        key = event.document_key
        with self._lock:
            lifetime = self.current_lifetime(key)
            if event.op is ChangeOp.UPSERT and event.raw_pointer != self.rawstore.pointer_for(
                key, event.version, lifetime=lifetime
            ):
                raise ValueError(f"{key} original belongs to a closed lifetime")
            delivery = self.bus.publish(event.model_copy(update={"original_lifetime": lifetime}))
            self.acceptance.accept(delivery)
            return delivery

    @classmethod
    def in_memory(cls, partitions: int = 3) -> ChangeLog:
        return cls(InMemoryEventBus(partitions), InMemoryRegistry(), InMemoryRawStore())

    def known(self, source: str) -> Callable[[], dict[str, ListingVersion]]:
        """What ``ekb demo s3``'s listing compares ``source`` against: the registry's live items."""
        return lambda: self.registry.known(source)

    def accepted(self, source: str) -> Callable[[], dict[str, ListingVersion]]:
        """What the connectors compare ``source`` against: the live items the bus has
        accepted, processed or not, so nothing pending is sent twice."""
        return lambda: known_versions(self.acceptance.records(source))

    def record_processed(self) -> int:
        """Stand-in worker: record every pending event in the registry; return how many."""
        return drain(self.bus, REGISTRY_GROUP, lambda d: record_event(self.registry, d.event))
