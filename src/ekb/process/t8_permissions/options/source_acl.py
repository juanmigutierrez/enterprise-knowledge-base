"""T8 option ``source_acl``: the source's readers, copied as the source wrote them.

The starting choice in ``config/default.yaml``. T1 has already mapped every
user to the identity provider's ID and left out the ones it couldn't (fail
closed, ``ekb.connect.people``), so there is nothing left to translate for the
docs tool: its pages name flat user and group lists. A source with richer
permissions (Confluence space permissions intersected with page restrictions,
SharePoint inheritance) would flatten them here, in an option of its own.

**Groups are kept as written, even unknown ones**, and reported. A group no
identity user is in admits nobody, so keeping it opens nothing; dropping it
would mean that when the group is created, its members don't get what the
source granted them until the page is processed again. Users with no record,
and users who have left, are reported the same way: :func:`ekb.contracts.admits`
already gives them nothing.

**An ACL that names nobody is copied too**, and the note says so: such a chunk
is published and readable by no one, which is what the source said.

Later options: ``expand_groups`` (every reader stored as a user, for card
4.13's comparison) and a Cedar policy that narrows readers by sensitivity.
"""

from __future__ import annotations

from ekb.contracts import ChangeEvent, readers_now
from ekb.platform.registry import register
from ekb.process.t8_permissions.port import MembershipDirectory, Resolution

VERSION = 1


@register("t8_permissions", "source_acl")
class SourceAclResolver:
    @property
    def resolver_id(self) -> str:
        return f"source_acl@{VERSION}"

    def resolve(self, event: ChangeEvent, directory: MembershipDirectory) -> Resolution:
        acl = event.acl
        memberships = directory.memberships()
        known_groups = {group for m in memberships for group in m.groups}
        unknown_groups = tuple(sorted(acl.groups - known_groups))
        unknown_users: list[str] = []
        inactive_users: list[str] = []
        for user in sorted(acl.users):
            membership = directory.membership(user)
            if membership is None:
                unknown_users.append(user)
            elif not membership.active:
                inactive_users.append(user)
        return Resolution(
            acl=acl,
            resolver_id=self.resolver_id,
            unknown_groups=unknown_groups,
            unknown_users=tuple(unknown_users),
            inactive_users=tuple(inactive_users),
            note=_note(
                len(readers_now(acl, memberships)),
                not acl.users and not acl.groups,
                unknown_groups,
                unknown_users,
                inactive_users,
            ),
        )


def _note(
    readers: int,
    empty: bool,
    unknown_groups: tuple[str, ...],
    unknown_users: list[str],
    inactive_users: list[str],
) -> str:
    if empty:
        return "readers copied from the source: it names nobody, so no one can read it"
    who = "1 person" if readers == 1 else f"{readers} people"
    parts = [f"readers copied from the source: {who} can read it now"]
    if unknown_groups:
        parts.append(f"no one is in {', '.join(unknown_groups)} (unknown to the identity provider)")
    if unknown_users:
        parts.append(f"no identity record for {', '.join(unknown_users)}")
    if inactive_users:
        parts.append(f"{', '.join(inactive_users)} left: they read nothing")
    return "; ".join(parts)
