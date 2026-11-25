"""``q2_prefilter: acl``: the person's user ID and current groups, from the permission store.

The groups come from the identity provider's user records through the change
log (S11), so a group change or a leaver takes effect on the **next** search,
without rewriting a single chunk. The chunks keep groups as groups (D8), and
:func:`ekb.contracts.admits` matches them against these principals in the store.

Three answers, each with its reason:

- an active person: their user ID and groups (``reads as alice + all-staff,
  it-employees``);
- a person who has left: nothing, whatever groups their record keeps (S1c);
- a user ID the identity provider doesn't know: nothing.

Cedar policies on top (sensitivity, which agent is asking) are the
``acl_and_cedar`` option, S15.
"""

from __future__ import annotations

from ekb.access.query.q2_prefilter.port import MembershipLookup
from ekb.contracts import Scope
from ekb.platform.registry import register

VERSION = 1


@register("q2_prefilter", "acl")
class AclPreFilter:
    def __init__(self, permissions: MembershipLookup) -> None:
        self._permissions = permissions

    @property
    def prefilter_id(self) -> str:
        return f"acl@{VERSION}"

    def scope(self, user_id: str) -> Scope:
        membership = self._permissions.membership(user_id)
        if membership is None:
            reason = "unknown to the identity provider: reads nothing"
        elif not membership.active:
            reason = "inactive (has left): reads nothing, whatever groups the record keeps"
        else:
            groups = ", ".join(sorted(membership.groups)) or "no groups"
            reason = f"reads as {user_id} + {groups}"
        principals = membership.principals if membership is not None else None
        return Scope(
            user_id=user_id, principals=principals, reason=reason, prefilter_id=self.prefilter_id
        )
