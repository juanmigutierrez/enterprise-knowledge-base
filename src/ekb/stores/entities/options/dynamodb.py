"""DynamoDB entity store: entities by ID, and which record defines each (S17).

The in-memory option's two dicts, as two tables, on DynamoDB Local locally and
DynamoDB on AWS:

======================  ==========================  ===============================
table                   key                         holds
======================  ==========================  ===============================
``ekb-entities``        ``entity_id``               ``kind``, the entity as JSON
``ekb-entity-records``  ``source_key`` (a record)   the ``entity_id`` it defines
======================  ==========================  ===============================

**Why a second table, not a secondary index.** A purge only knows the record's
key (``hr_api:employees/E-1003``), and must find the entity it defined. A
secondary index would answer that, but never with a strongly consistent read,
so a purge right after a write could miss it. The second table is read like
the first, and written in an order that is safe if a process stops half way:
the record's line before the entity, and removed after it. The worst a crash
leaves is a line naming an entity that is gone, which the next purge or write
of that record clears; never an entity no purge can find.

``entities`` scans the table (a few dozen entities in the POC).
"""

from __future__ import annotations

from ekb.contracts import Entity, EntityKind
from ekb.platform.aws import DynamoDBClient, Table, dynamodb_client
from ekb.platform.infra import scoped
from ekb.platform.registry import register

ENTITIES, RECORDS = "ekb-entities", "ekb-entity-records"


@register("entities", "dynamodb")
class DynamoEntityStore:
    def __init__(
        self,
        *,
        namespace: str | None = None,
        url: str | None = None,
        client: DynamoDBClient | None = None,
    ) -> None:
        client = client or dynamodb_client(url)
        self.entities_table = Table(client, scoped(ENTITIES, namespace), "entity_id")
        self.records_table = Table(client, scoped(RECORDS, namespace), "source_key")

    def put(self, entity: Entity) -> Entity | None:
        before = self._pop_source(entity.source_key)
        holder = self.get(entity.entity_id)
        if holder is not None and holder.source_key != entity.source_key:
            # Another record defined this ID before: the newer record owns it now.
            self.records_table.delete(holder.source_key)
        self.records_table.put({"source_key": entity.source_key, "entity_id": entity.entity_id})
        self.entities_table.put(
            {
                "entity_id": entity.entity_id,
                "kind": entity.kind.value,
                "doc": entity.model_dump_json(),
            }
        )
        return before

    def remove(self, source_key: str) -> Entity | None:
        return self._pop_source(source_key)

    def get(self, entity_id: str) -> Entity | None:
        item = self.entities_table.get(entity_id)
        return Entity.model_validate_json(str(item["doc"])) if item else None

    def entities(self, kind: EntityKind | None = None) -> tuple[Entity, ...]:
        found = [
            Entity.model_validate_json(str(item["doc"]))
            for item in self.entities_table.scan()
            if kind is None or item.get("kind") == kind.value
        ]
        return tuple(sorted(found, key=lambda e: e.entity_id))

    def _pop_source(self, source_key: str) -> Entity | None:
        line = self.records_table.get(source_key)
        if line is None:
            return None
        entity_id = str(line["entity_id"])
        old = self.entities_table.get(entity_id)
        entity = Entity.model_validate_json(str(old["doc"])) if old else None
        if entity is not None and entity.source_key == source_key:
            self.entities_table.delete(entity_id)
        else:
            entity = None  # the ID moved to a newer record: it is not this record's to remove
        self.records_table.delete(source_key)
        return entity

    def destroy(self) -> None:
        """Drop both tables (the end of a demo or test run)."""
        self.entities_table.drop()
        self.records_table.drop()
