"""Port for 3 · Change log: the acceptance log. Every option implements :class:`AcceptanceLog`.

The document registry remembers what the knowledge base has **processed**. The
change log needs a second memory, of what the bus has **accepted**, before any
worker has seen it. Per ``document_key`` it holds:

- **the accepted record**: :func:`~ekb.changelog.registry.port.record_after`
  applied to every published event. The webhook connector and the full
  listings compare a source with it (``known``), so an item the bus already has
  is not sent twice, and an item the source no longer lists is deleted.
- **the lifetime**: 0 at first, one more each time a delete is accepted.
  Originals are stored per lifetime (the raw store), and the search and
  permission stores hide anything from a closed lifetime. So the deletion gate
  holds from the moment a delete is accepted, before a worker purges anything.
- **the latest delivery** (partition and offset): an item the worker hid while
  recovering it is released only once its latest event is done.

Until S17 the change log kept these in its own process. Once the connectors,
the worker and the Knowledge API run as separate services, every one of them
must see the same lifetimes, hence the ``dynamodb`` option.

**Publish first, then accept.** The change log publishes an event, then
records it here. A crash between the two leaves an event on the bus this log
does not know yet; the next full listing still finds the item here and sends
its delete again (a test). The other order could close a lifetime, and hide a
page, for a delete the bus never received.

**One writer.** Only the change log of the process running the connectors
writes here (``ekb connect``, or ``ekb api`` / ``ekb inspector`` running
everything in one process); everyone else only reads. So an update is a plain
read, then write, with no conditional write.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ekb.changelog.bus.port import Delivery
from ekb.changelog.registry.port import DocumentRecord, record_after
from ekb.contracts import ChangeOp


@dataclass(frozen=True)
class Accepted:
    """What the bus has accepted for one item."""

    record: DocumentRecord
    """The item as the published events describe it (tombstones included)."""
    lifetime: int
    """The lifetime open now: how many deletes of this item the bus has accepted."""
    partition: int
    offset: int
    """Where the item's latest event sits on the bus."""

    @property
    def latest(self) -> tuple[int, int]:
        return (self.partition, self.offset)


def accepted_after(previous: Accepted | None, delivery: Delivery) -> Accepted:
    """The entry once ``delivery`` is accepted. Pure, like ``record_after``."""
    lifetime = previous.lifetime if previous else 0
    if delivery.event.op is ChangeOp.DELETE:
        lifetime += 1
    return Accepted(
        record=record_after(previous.record if previous else None, delivery.event),
        lifetime=lifetime,
        partition=delivery.partition,
        offset=delivery.offset,
    )


@runtime_checkable
class AcceptanceLog(Protocol):
    """Per item, what the bus has accepted, its open lifetime and its latest delivery."""

    def get(self, document_key: str) -> Accepted | None:
        """The item's entry, or ``None`` if the bus never accepted an event for it."""
        ...

    def accept(self, delivery: Delivery) -> Accepted:
        """Record an event the bus has just accepted; return the item's new entry."""
        ...

    def records(self, source: str | None = None) -> list[DocumentRecord]:
        """Every accepted record (of one source), tombstones included, by ``document_key``."""
        ...
