"""S1c · Sources: the mock identity provider, where a group change is a permission change."""

from __future__ import annotations

from typing import Annotated

import httpx
import typer

from ekb.app.demos._app import demo_app
from ekb.app.demos._mocks import Echo, MockStartError, call, fail, mock_client

MODULE = "sources.mock_idp"
USER = "alice"
GROUP = "it-employees"
LEAVER = "olivia"


def run_s1c(client: httpx.Client, echo: Echo = typer.echo) -> None:
    """The S1c story against a running mock identity provider (any ``httpx.Client``)."""
    call(client, "POST", "/reset")

    echo("S1c · The mock identity provider (stands in for Okta / Entra ID)\n")
    feed = call(client, "GET", "/events")
    users = call(client, "GET", "/scim/v2/Users")
    groups = call(client, "GET", "/scim/v2/Groups")
    echo(f"1. {users['totalResults']} users in {groups['totalResults']} groups:\n")
    for group in groups["Resources"]:
        echo(f"   {group['id']:<16} {len(group['members']):>2}  {', '.join(group['members'])}")
    echo(f"\n   The sync saved cursor {feed['cursor']} before listing users and groups.")
    echo("   Replaying from there includes changes made during the listing.\n")

    before = call(client, "GET", f"/scim/v2/Users/{USER}")
    call(client, "DELETE", f"/scim/v2/Groups/{GROUP}/members/{USER}")
    after = call(client, "GET", f"/scim/v2/Users/{USER}")
    echo(f"2. {before['displayName']} moves off an Italian contract: removed from {GROUP}.")
    echo(f"   groups {', '.join(before['groups'])} -> {', '.join(after['groups'])}")
    echo(f"   {USER} loses access to pages she could read only through {GROUP}.")
    echo("   Direct grants and grants through her other groups still apply.\n")

    call(client, "DELETE", f"/scim/v2/Groups/{GROUP}/members/{USER}")
    echo("3. The same removal again: accepted, but nothing changed, so no event.\n")

    left = call(client, "POST", f"/scim/v2/Users/{LEAVER}/deactivate")
    echo(f"4. {left['displayName']} leaves the company: deactivated, not deleted.")
    echo(f"   active={left['active']}, still in {', '.join(left['groups'])}")
    echo("   Memberships stay, so later steps must treat an inactive user as having")
    echo("   no access at all, whatever their groups say.\n")

    events = call(client, "GET", "/events", params={"since": feed["cursor"]})
    echo(f"5. The event feed since cursor {feed['cursor']}:")
    for e in events["events"]:
        group = f" {e['group']}" if e["group"] else ""
        echo(f"   #{e['seq']:<3} {e['event']:<22} {e['user_id']}{group}")
    echo("   In S2a the identity sync turns these into the product's ChangeEvent.\n")
    echo("Try it yourself: `uv run python -m sources.mock_idp`, then open")
    echo("http://127.0.0.1:8083/docs.")


@demo_app.command("s1c")
def s1c(
    url: Annotated[
        str | None,
        typer.Option(
            help="Use a mock identity provider that is already running, e.g. http://127.0.0.1:8083."
        ),
    ] = None,
) -> None:
    """S1c · Sources: remove a user from a group, deactivate a leaver, read the events."""
    try:
        with mock_client(MODULE, url) as client:
            run_s1c(client)
    except (MockStartError, httpx.HTTPError) as exc:
        raise fail(exc) from exc
