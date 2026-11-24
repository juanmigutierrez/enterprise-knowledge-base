"""Port for 2 · Connect. Every connector option implements :class:`Connector`.

A connector's only job is to notice changes in one source and hand them on as
``ChangeEvent``s. How it notices differs (a webhook, a delta query, a database
log, a full listing); what it hands on never does. That is why everything
downstream depends on ``ChangeEvent`` and nothing on the source type (D3).

Two things are injected rather than built in, so the connector stays the same
when the infrastructure changes:

- ``EventSink``: where events go. A list in tests and demos; the event bus
  from S3 on.
- ``OriginalStore``: where the fetched original is kept, returning the pointer
  an upsert must carry. An in-memory dict in S2; the raw store (S3 API) from S3.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from ekb.contracts import ChangeEvent

EventSink = Callable[[ChangeEvent], None]
"""Receives each normalized event, in the order the connector produced them."""

OriginalStore = Callable[[str, str, bytes], str]
"""``(document_key, version, original_bytes) -> raw_pointer``."""


@runtime_checkable
class Connector(Protocol):
    """Turns one source's changes into ``ChangeEvent``s."""

    @property
    def source(self) -> str:
        """The ``source`` written into every event, e.g. ``hr_docs``."""
        ...

    def sync(self) -> int:
        """Bring the knowledge base up to date with the source now.

        Emits the events to the sink and returns how many it emitted. A push
        connector processes what it has received; a pull connector asks the
        source.
        """
        ...
