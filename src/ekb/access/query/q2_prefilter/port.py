"""Port for Q2 · Pre-filter. Every option implements :class:`PreFilter`.

A pre-filter answers one question: **what does this person read with, right
now?** It returns a :class:`~ekb.contracts.Scope`: their principals (user ID
and current groups) or ``None``, with the reason in plain words. Q3 hands the
principals to the search store, which ranks only the chunks they admit.

Q2 decides nothing about the question: whoever asks the same thing gets the
same ranking over the chunks *they* may read, and nothing else.

**Fail closed.** A person the identity provider doesn't know, or who has left,
gets ``principals=None``: they read nothing, and Q3 does not even ask the store.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ekb.contracts import Membership, Scope


@runtime_checkable
class MembershipLookup(Protocol):
    """Who someone is now: all a pre-filter needs from the permission store (S11)."""

    def membership(self, user_id: str) -> Membership | None: ...


@runtime_checkable
class PreFilter(Protocol):
    """Turns the person asking into the scope a search filters by."""

    @property
    def prefilter_id(self) -> str:
        """The option and its version, e.g. ``acl@1``."""
        ...

    def scope(self, user_id: str) -> Scope: ...
