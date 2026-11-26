"""DynamoDB permission store: memberships by user, readers by document (S17).

The in-memory option's dicts and its set of hidden items, as four tables:

==========================  ================  ======================================
table                       key               holds
==========================  ================  ======================================
``ekb-memberships``         ``user_id``       the membership, as JSON
``ekb-membership-records``  ``source_key``    the ``user_id`` the identity record defines
``ekb-membership-hidden``   ``source_key``    an identity record hidden during recovery
``ekb-readers``             ``document_key``  T8's :class:`DocumentAcl`, as JSON
==========================  ================  ======================================

Every read is strongly consistent, which the gates need: a leaver's purge
must remove the membership the next question reads. That is why a purge finds
the user through ``ekb-membership-records``, not a secondary index (see the
entity store's docstring for the safe write order, the same here).

**Whose membership counts** is decided on every read, as in memory: not one
whose record is hidden, and not one from a closed change-log lifetime
(``current_lifetime``, the acceptance log's, S11 and S17).
"""

from __future__ import annotations

from collections.abc import Callable

from ekb.contracts import DocumentAcl, Membership, Principals
from ekb.platform.aws import DynamoDBClient, Table, dynamodb_client
from ekb.platform.infra import scoped
from ekb.platform.registry import register

MEMBERSHIPS, RECORDS, HIDDEN, READERS = (
    "ekb-memberships",
    "ekb-membership-records",
    "ekb-membership-hidden",
    "ekb-readers",
)


@register("permissions", "dynamodb")
class DynamoPermissionStore:
    def __init__(
        self,
        current_lifetime: Callable[[str], int] | None = None,
        *,
        namespace: str | None = None,
        url: str | None = None,
        client: DynamoDBClient | None = None,
    ) -> None:
        self._current_lifetime = current_lifetime
        client = client or dynamodb_client(url)
        self.users = Table(client, scoped(MEMBERSHIPS, namespace), "user_id")
        self.records = Table(client, scoped(RECORDS, namespace), "source_key")
        self.hidden = Table(client, scoped(HIDDEN, namespace), "source_key")
        self.readers_table = Table(client, scoped(READERS, namespace), "document_key")

    # ------------------------------------------------------------ memberships

    def set_visible(self, document_key: str, *, visible: bool) -> None:
        if visible:
            self.hidden.delete(document_key)
        else:
            self.hidden.put({"source_key": document_key})

    def put_membership(self, membership: Membership) -> Membership | None:
        before = self._pop_source(membership.source_key)
        holder = self._stored(membership.user_id)
        if holder is not None and holder.source_key != membership.source_key:
            # Another record defined this user before: the newer record owns it now.
            self.records.delete(holder.source_key)
            before = before or holder
        self.records.put({"source_key": membership.source_key, "user_id": membership.user_id})
        self.users.put({"user_id": membership.user_id, "doc": membership.model_dump_json()})
        return before

    def remove_membership(self, source_key: str) -> Membership | None:
        return self._pop_source(source_key)

    def membership(self, user_id: str) -> Membership | None:
        member = self._stored(user_id)
        if member is None or self.hidden.get(member.source_key) is not None:
            return None
        return member if self._current(member) else None

    def memberships(self) -> tuple[Membership, ...]:
        hidden = {str(item["source_key"]) for item in self.hidden.scan()}
        found = [Membership.model_validate_json(str(item["doc"])) for item in self.users.scan()]
        return tuple(
            m
            for m in sorted(found, key=lambda m: m.user_id)
            if m.source_key not in hidden and self._current(m)
        )

    def principals(self, user_id: str) -> Principals | None:
        membership = self.membership(user_id)
        return membership.principals if membership is not None else None

    def _current(self, member: Membership) -> bool:
        return (
            self._current_lifetime is None
            or self._current_lifetime(member.source_key) == member.original_lifetime
        )

    def _stored(self, user_id: str) -> Membership | None:
        item = self.users.get(user_id)
        return Membership.model_validate_json(str(item["doc"])) if item else None

    def _pop_source(self, source_key: str) -> Membership | None:
        line = self.records.get(source_key)
        if line is None:
            return None
        user_id = str(line["user_id"])
        member = self._stored(user_id)
        if member is not None and member.source_key == source_key:
            self.users.delete(user_id)
        else:
            member = None  # the user moved to a newer record: not this record's to remove
        self.records.delete(source_key)
        return member

    # ------------------------------------------------------- document readers

    def put_readers(self, record: DocumentAcl) -> DocumentAcl | None:
        before = self.readers(record.document_key)
        self.readers_table.put(
            {"document_key": record.document_key, "doc": record.model_dump_json()}
        )
        return before

    def readers(self, document_key: str) -> DocumentAcl | None:
        item = self.readers_table.get(document_key)
        return DocumentAcl.model_validate_json(str(item["doc"])) if item else None

    def all_readers(self) -> tuple[DocumentAcl, ...]:
        found = [DocumentAcl.model_validate_json(str(i["doc"])) for i in self.readers_table.scan()]
        return tuple(sorted(found, key=lambda r: r.document_key))

    def forget_readers(self, document_key: str) -> DocumentAcl | None:
        old = self.readers_table.delete(document_key)
        return DocumentAcl.model_validate_json(str(old["doc"])) if old else None

    def destroy(self) -> None:
        """Drop the four tables (the end of a demo or test run)."""
        for table in (self.users, self.records, self.hidden, self.readers_table):
            table.drop()
