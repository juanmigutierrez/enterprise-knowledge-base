"""One canonical person ID: the identity provider's user ID (D3).

Sources name people their own way: the HR API says ``E-1001``, the database's
leave rows say ``E-1001``, the docs tool and the identity provider say
``alice``. Every person ID the product writes into an event (``person_id``,
``changed_by``, the users in an ACL) must be an identity provider user ID, so
permissions and history line up across sources.

:class:`PersonMap` does the mapping. HR records link to identity users **1-1 by
full name** (the rule decided in S1b and kept unique by both mocks).

**Nothing is mapped silently.** When an ID can't be mapped:

- the map reloads both sources once (a new joiner may have arrived since);
- if it still fails, the ID is reported: added to :attr:`PersonMap.unmapped`
  and logged as a warning;
- and the caller fails closed: no ``person_id``, no ``changed_by``, and the
  unknown user is left out of the ACL. Less access, never more.

:meth:`PersonMap.gaps` is the double check across whole sources: every HR
employee must have an identity user. The demo prints it, and a test fails if
the synthetic data has any gap.

**A gap that closes later is repaired (S16).** HR usually hires before the
identity provider has an account, so a new employee's first event carries no
person, and their record defines no entity. The HR full listing asks
:meth:`PersonMap.maps_now` about every employee it reported, and sends the
record again once they map; :meth:`PersonMap.settle` then takes them off the
report.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from ekb.connect.hr_api import HrApi
from ekb.connect.identity_api import IdentityApi

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class People:
    """Who each source knows, by full name."""

    users: Mapping[str, str]  # identity user ID -> full name
    employees: Mapping[str, str] = field(default_factory=dict)  # HR employee ID -> full name


PeopleLoader = Callable[[], People]


def load_people(identity: IdentityApi, hr: HrApi | None = None) -> PeopleLoader:
    """A loader reading the identity provider, and the HR API if given."""

    def load() -> People:
        users = {u.id: u.display_name for u in identity.list_users()}
        employees = {r.id: r.name for r in hr.list("employees").records} if hr else {}
        return People(users=users, employees=employees)

    return load


class PersonMap:
    def __init__(self, load: PeopleLoader) -> None:
        self._load = load
        self._lock = threading.Lock()
        self._users: frozenset[str] = frozenset()
        self._employees: dict[str, str] = {}
        self._by_employee: dict[str, str] = {}
        self._unmapped: set[str] = set()
        self.refresh()

    def refresh(self) -> None:
        """Reload both sources. Refuses ambiguous names rather than guess."""
        people = self._load()
        user_counts = Counter(people.users.values())
        employee_counts = Counter(people.employees.values())
        by_name = {name: uid for uid, name in people.users.items() if user_counts[name] == 1}
        with self._lock:
            self._users = frozenset(people.users)
            self._employees = dict(people.employees)
            self._by_employee = {
                eid: by_name[name]
                for eid, name in people.employees.items()
                if name in by_name and employee_counts[name] == 1
            }

    # ------------------------------------------------------------------ lookups

    def user(self, user_id: str | None) -> str | None:
        """``user_id`` if the identity provider knows it, else ``None`` (reported)."""
        if user_id is None:
            return None
        return self._resolve(f"user:{user_id}", lambda: user_id if user_id in self._users else None)

    def for_employee(
        self, employee_id: str, *, name: str | None = None, refresh_on_miss: bool = True
    ) -> str | None:
        """The identity user for an HR employee ID, else ``None`` (reported)."""

        def lookup() -> str | None:
            if name is not None and self._employees.get(employee_id) != name:
                return None
            return self._by_employee.get(employee_id)

        return self._resolve(f"employee:{employee_id}", lookup, refresh_on_miss=refresh_on_miss)

    def maps_now(self, employee_id: str, name: str) -> str | None:
        """The identity user for this employee as of the last load, without reloading or
        reporting a miss: for the listing's check on employees it reported before."""
        with self._lock:
            if self._employees.get(employee_id) != name:
                return None
            return self._by_employee.get(employee_id)

    def settle(self, label: str) -> None:
        """``label`` (``employee:E-1013``) was sent again with its person: no longer a gap."""
        with self._lock:
            self._unmapped.discard(label)

    def users_in(self, user_ids: Iterable[str]) -> frozenset[str]:
        """The known users among ``user_ids``; unknown ones are reported and left out."""
        return frozenset(u for u in user_ids if self.user(u) is not None)

    # ------------------------------------------------------------------ checks

    @property
    def unmapped(self) -> frozenset[str]:
        """Every ID that could not be mapped so far, e.g. ``employee:E-1013``."""
        with self._lock:
            return frozenset(self._unmapped)

    def gaps(self) -> list[str]:
        """HR employees with no identity user: must be empty for the mapping to be complete."""
        with self._lock:
            return sorted(f"employee:{e}" for e in self._employees if e not in self._by_employee)

    def coverage(self) -> tuple[int, int]:
        """``(mapped, total)`` HR employees."""
        with self._lock:
            return len(self._by_employee), len(self._employees)

    def _resolve(
        self, label: str, lookup: Callable[[], str | None], *, refresh_on_miss: bool = True
    ) -> str | None:
        with self._lock:
            found = lookup()
        if found is not None:
            return found
        if refresh_on_miss:
            self.refresh()  # a joiner or a new hire may have arrived since the last load
        with self._lock:
            found = lookup()
            if found is None:
                self._unmapped.add(label)
        if found is None:
            logger.warning("unmapped person %s: event emitted without it (fail closed)", label)
        return found
