"""S16 · The self-maintenance story: one scripted change in a source per step.

The assignment asks for a knowledge base that **maintains itself**: edits,
deletions and permission changes in the sources reach every answer with no
manual step. This module is that requirement written down as a script, shared
by ``ekb demo lifecycle``, the inspector's **Lifecycle** screen and the gate
tests (``tests/gates/``), so all three tell the same story::

    1 add_page          dana creates HR-PL-11 (mentoring), all staff may read it
    2 edit_page         dana changes "2 times a month" to 3
    3 revoke_page       dana narrows HR-PL-11 to hr-team
    4 remove_from_group dana removes lucia from hr-team
    5 hire_record       bob adds Sara Esposito to the HR system
    6 hire_account      dana creates Sara's identity account, after the HR record
    7 leaver            bob deletes Lucía's HR record (only the full listing sees it)
    8 delete_page       dana deletes HR-PL-99, the superseded travel policy

Each action changes **a source only**, as its owner would, through the same
HTTP API a person's tool would call, with the person in ``X-Actor``. Nothing
here touches the knowledge base: the connectors notice the change, the worker
applies it, and the query path answers from the result. That is the point.

Each action also says which connector catches it (``caught_by``) and what
should change (``expect``), for the demo, the screen and the walkthrough.

The probes are the questions asked before and after each step: who asks what,
and which page the answer depends on.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

MENTORING = "HR-PL-11"
MENTORING_KEY = f"hr_docs:{MENTORING}"
OLD_TRAVEL = "HR-PL-99"
OLD_TRAVEL_KEY = f"hr_docs:{OLD_TRAVEL}"
NEW_TRAVEL_KEY = "hr_docs:HR-PL-06"
RESTRICTED_KEY = "hr_docs:HR-CMP-01"
"""The salary bands: only bob (hr-compensation) may ever see them (the leakage gate)."""
LEAVER_EMPLOYEE = "E-1009"
LEAVER = "lucia"
"""Lucía Navarro: the People team member removed from hr-team, then the leaver."""
HIRE_EMPLOYEE = "E-1013"
HIRE = "sara"
HIRE_NAME = "Sara Esposito"

MENTORING_BODY = """\
# Mentoring programme

Mentoring groups meet **{times} times a month**, online or in the office.

## Joining a group
Ask Lucía Navarro in the People team to join a group. Groups have five people at most.

## Coordination
From December, {coordinator} coordinates the sessions and keeps the calendar.
"""


def mentoring_body(times: int) -> str:
    return MENTORING_BODY.format(times=times, coordinator=HIRE_NAME)


@dataclass(frozen=True)
class Sources:
    """The three mock services an action may change (the database has no scripted step)."""

    docs: httpx.Client
    hr: httpx.Client
    idp: httpx.Client


def _call(client: httpx.Client, method: str, path: str, actor: str, **kwargs: Any) -> None:
    response = client.request(method, path, headers={"X-Actor": actor}, **kwargs)
    response.raise_for_status()


@dataclass(frozen=True)
class Action:
    """One scripted change in one source."""

    id: str
    title: str
    actor: str
    source: str
    """Which system changes, in plain words."""
    change: str
    """What the person does, in plain words."""
    caught_by: str
    """Which connector notices it, and how fast."""
    listing: bool
    """Only a full listing can see it: the sync round must include the listings."""
    expect: str
    """What should change in the answers and the stores."""
    apply: Callable[[Sources], None] = field(repr=False, compare=False)
    after: tuple[str, ...] = ()
    """Actions that must have been applied first (the story's order)."""


def _add_page(s: Sources) -> None:
    _call(
        s.docs,
        "POST",
        "/pages",
        "dana",
        json={
            "id": MENTORING,
            "title": "Mentoring programme",
            "language": "en",
            "department": "people",
            "acl": {"groups": ["all-staff"]},
            "body": mentoring_body(2),
        },
    )


def _edit_page(s: Sources) -> None:
    _call(s.docs, "PUT", f"/pages/{MENTORING}", "dana", json={"body": mentoring_body(3)})


def _revoke_page(s: Sources) -> None:
    _call(s.docs, "PUT", f"/pages/{MENTORING}/acl", "dana", json={"groups": ["hr-team"]})


def _remove_from_group(s: Sources) -> None:
    _call(s.idp, "DELETE", f"/scim/v2/Groups/hr-team/members/{LEAVER}", "dana")


def _hire_record(s: Sources) -> None:
    _call(
        s.hr,
        "POST",
        "/employees",
        "bob",
        json={
            "id": HIRE_EMPLOYEE,
            "name": HIRE_NAME,
            "work_email": "sara.esposito@example.com",
            "team_id": "T-PPL",
            "office": "Milan",
            "manager_id": "E-1002",
            "start_date": "2026-12-01",
        },
    )


def _hire_account(s: Sources) -> None:
    _call(
        s.idp,
        "POST",
        "/scim/v2/Users",
        "dana",
        json={"id": HIRE, "name": HIRE_NAME, "location": "Milan", "groups": ["all-staff"]},
    )


def _leaver(s: Sources) -> None:
    _call(s.hr, "DELETE", f"/employees/{LEAVER_EMPLOYEE}", "bob")


def _delete_page(s: Sources) -> None:
    _call(s.docs, "DELETE", f"/pages/{OLD_TRAVEL}", "dana")


ACTIONS: tuple[Action, ...] = (
    Action(
        id="add_page",
        title="Add a page",
        actor="dana",
        source="docs tool",
        change=f"dana creates {MENTORING}, the mentoring programme: groups meet 2 times a "
        "month; everyone in all-staff may read it. It names Lucía Navarro, and Sara Esposito, "
        "who has not been hired yet.",
        caught_by="the docs webhook, at once (the full listing would catch it too)",
        listing=False,
        expect=f"alice finds {MENTORING}, cited; only Lucía (and the People team) is linked",
        apply=_add_page,
    ),
    Action(
        id="edit_page",
        title="Edit the page",
        actor="dana",
        source="docs tool",
        change=f"dana changes {MENTORING}: groups now meet 3 times a month.",
        caught_by="the docs webhook, at once",
        listing=False,
        expect="the answer says 3; alice's cached answer is dropped, because the source "
        "changed; the old text is never returned",
        apply=_edit_page,
        after=("add_page",),
    ),
    Action(
        id="revoke_page",
        title="Revoke on the page",
        actor="dana",
        source="docs tool",
        change=f"dana narrows {MENTORING}'s readers from all-staff to hr-team.",
        caught_by="the docs webhook, at once: T2's fast lane",
        listing=False,
        expect=f"alice gets no trace of {MENTORING}, cached or fresh, even before the worker "
        "runs; lucia (hr-team) still gets it; no vector changes",
        apply=_revoke_page,
        after=("add_page",),
    ),
    Action(
        id="remove_from_group",
        title="Revoke by group",
        actor="dana",
        source="identity provider",
        change="dana removes lucia from hr-team.",
        caught_by="the identity sync, on its next poll",
        listing=False,
        expect=f"lucia no longer gets {MENTORING}, even before the sync (Q4 asks the identity "
        "provider now); no chunk is rewritten",
        apply=_remove_from_group,
        after=("revoke_page",),
    ),
    Action(
        id="hire_record",
        title="A hire, in HR",
        actor="bob",
        source="HR record API",
        change=f"bob adds {HIRE_NAME} ({HIRE_EMPLOYEE}) to the People team. She has no "
        "identity account yet.",
        caught_by="the HR delta, on its next poll",
        listing=False,
        expect="no entity yet: an employee with no identity account is no one to link to "
        "(fail closed)",
        apply=_hire_record,
        after=("add_page",),
    ),
    Action(
        id="hire_account",
        title="A hire, her account",
        actor="dana",
        source="identity provider",
        change=f"dana creates {HIRE_NAME}'s identity account (sara), after the HR record.",
        caught_by="the identity sync, then the HR full listing, which sends her record again "
        "now that it maps to a person",
        listing=True,
        expect=f"person:sara joins the entity store, and {MENTORING}, which already named "
        "her, is re-linked without being edited",
        apply=_hire_account,
        after=("hire_record",),
    ),
    Action(
        id="leaver",
        title="A leaver",
        actor="bob",
        source="HR record API",
        change=f"bob deletes Lucía Navarro's HR record ({LEAVER_EMPLOYEE}).",
        caught_by="the HR full listing only: the delta never reports a delete",
        listing=True,
        expect=f"person:lucia leaves the entity store, and {MENTORING} loses her link "
        "without being edited",
        apply=_leaver,
        after=("add_page",),
    ),
    Action(
        id="delete_page",
        title="Delete a page",
        actor="dana",
        source="docs tool",
        change=f"dana deletes {OLD_TRAVEL}, the superseded 2024 travel policy.",
        caught_by="the docs webhook, at once",
        listing=False,
        expect=f"{OLD_TRAVEL} is never cited again; nothing of it stays in the search store, "
        "the model cache or the answer cache",
        apply=_delete_page,
    ),
)
BY_ID = {action.id: action for action in ACTIONS}


@dataclass(frozen=True)
class Probe:
    """A question asked before and after each step, and the page it depends on."""

    user: str
    question: str
    page: str


MENTORING_QUESTION = "How often do mentoring groups meet?"
HOTEL_QUESTION = "What is the hotel limit per night?"
PROBES: tuple[Probe, ...] = (
    Probe("alice", MENTORING_QUESTION, MENTORING_KEY),
    Probe("lucia", MENTORING_QUESTION, MENTORING_KEY),
    Probe("bob", MENTORING_QUESTION, MENTORING_KEY),
    Probe("alice", HOTEL_QUESTION, OLD_TRAVEL_KEY),
)
USERS: tuple[str, ...] = tuple(dict.fromkeys(probe.user for probe in PROBES))


class ActionRefusedError(ValueError):
    """An action that cannot run now: unknown, already applied, or out of order."""


def check(action_id: str, applied: Sequence[str]) -> Action:
    """The action to apply next, or why it can't be."""
    action = BY_ID.get(action_id)
    if action is None:
        raise ActionRefusedError(f"no scripted action {action_id!r}")
    if action_id in applied:
        raise ActionRefusedError(f"{action_id} was already applied")
    missing = [a for a in action.after if a not in applied]
    if missing:
        raise ActionRefusedError(f"{action_id} needs {', '.join(missing)} first")
    return action
