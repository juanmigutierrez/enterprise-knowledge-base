"""DynamoDB acceptance log: one item per document in ``ekb-accepted`` (S17).

Every process reads the same lifetimes from it: the connectors' change log
writes, the worker and the Knowledge API read (strongly consistent reads, so a
delete the connector has just accepted hides the page from the next search).

=================  =========================================================
attribute          holds
=================  =========================================================
``document_key``   the partition key
``source``         to list one source (a scan with a filter: fine at POC size)
``lifetime``       the open lifetime (a number)
``partition``      where the latest event sits on the bus
``offset``
``doc``            the accepted record, as JSON
=================  =========================================================
"""

from __future__ import annotations

from ekb.changelog.acceptance.port import Accepted, accepted_after
from ekb.changelog.bus.port import Delivery
from ekb.changelog.registry.port import DocumentRecord
from ekb.platform.aws import DynamoDBClient, Table, Value, dynamodb_client
from ekb.platform.infra import scoped
from ekb.platform.registry import register

TABLE = "ekb-accepted"


def _entry(item: dict[str, Value]) -> Accepted:
    return Accepted(
        record=DocumentRecord.model_validate_json(str(item["doc"])),
        lifetime=int(item["lifetime"]),
        partition=int(item["partition"]),
        offset=int(item["offset"]),
    )


@register("acceptance", "dynamodb")
class DynamoAcceptanceLog:
    def __init__(
        self,
        *,
        namespace: str | None = None,
        url: str | None = None,
        client: DynamoDBClient | None = None,
    ) -> None:
        self.table = Table(client or dynamodb_client(url), scoped(TABLE, namespace), "document_key")

    def get(self, document_key: str) -> Accepted | None:
        item = self.table.get(document_key)
        return _entry(item) if item else None

    def accept(self, delivery: Delivery) -> Accepted:
        entry = accepted_after(self.get(delivery.event.document_key), delivery)
        self.table.put(
            {
                "document_key": entry.record.document_key,
                "source": entry.record.source,
                "lifetime": entry.lifetime,
                "partition": entry.partition,
                "offset": entry.offset,
                "doc": entry.record.model_dump_json(),
            }
        )
        return entry

    def records(self, source: str | None = None) -> list[DocumentRecord]:
        records = [
            DocumentRecord.model_validate_json(str(item["doc"]))
            for item in self.table.scan()
            if source is None or item.get("source") == source
        ]
        return sorted(records, key=lambda r: r.document_key)

    def destroy(self) -> None:
        """Drop the table (the end of a demo or test run)."""
        self.table.drop()
