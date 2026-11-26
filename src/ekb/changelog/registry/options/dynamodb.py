"""DynamoDB document registry: one item per document in ``ekb-registry`` (S17).

The same port as the in-memory option, on DynamoDB Local locally and DynamoDB
on AWS (D13: "same code"). T2 reads and writes it in the worker; the full
listings read ``known`` through the acceptance log, not here.

=================  ==============================================
attribute          holds
=================  ==============================================
``document_key``   the partition key
``source``         to list one source (a scan with a filter)
``deleted``        a tombstone (a boolean), for a quick look
``doc``            the :class:`DocumentRecord`, as JSON
=================  ==============================================

``known`` and ``records`` scan the table: a few hundred items in the POC. At
scale a secondary index on ``source`` would replace the scan.
"""

from __future__ import annotations

from ekb.changelog.registry.port import DocumentRecord, known_versions
from ekb.contracts.listing import ListingVersion
from ekb.platform.aws import DynamoDBClient, Table, dynamodb_client
from ekb.platform.infra import scoped
from ekb.platform.registry import register

TABLE = "ekb-registry"


@register("registry", "dynamodb")
class DynamoRegistry:
    def __init__(
        self,
        *,
        namespace: str | None = None,
        url: str | None = None,
        client: DynamoDBClient | None = None,
    ) -> None:
        self.table = Table(client or dynamodb_client(url), scoped(TABLE, namespace), "document_key")

    def get(self, document_key: str) -> DocumentRecord | None:
        item = self.table.get(document_key)
        return DocumentRecord.model_validate_json(str(item["doc"])) if item else None

    def put(self, record: DocumentRecord) -> None:
        self.table.put(
            {
                "document_key": record.document_key,
                "source": record.source,
                "deleted": record.deleted,
                "doc": record.model_dump_json(),
            }
        )

    def known(self, source: str) -> dict[str, ListingVersion]:
        return known_versions(self.records(source))

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
