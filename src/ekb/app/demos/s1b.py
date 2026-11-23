"""S1b · Sources: the mock HR record API, a source you must ask, which never reports deletes."""

from __future__ import annotations

from collections import Counter
from typing import Annotated

import httpx
import typer

from ekb.app.demos._app import demo_app
from ekb.app.demos._mocks import Echo, MockStartError, call, fail, mock_client

MODULE = "sources.mock_hr_api"
MOVED = "E-1008"  # Javier Ruiz moves from Madrid to Milan
LEAVER = "E-1012"  # Olivia Chen leaves: manages nobody, leads no team
MANAGER = "E-1004"  # Marco Bianchi leads Engineering and manages three people.


def run_s1b(client: httpx.Client, echo: Echo = typer.echo) -> None:
    """The S1b story against a running mock HR record API (any ``httpx.Client``)."""
    call(client, "POST", "/reset")

    echo("S1b · The mock HR record API (stands in for Workday)\n")
    listing = call(client, "GET", "/employees")
    teams = {t["id"]: t["name"] for t in call(client, "GET", "/teams")["records"]}
    employees = listing["records"]
    echo(f"1. A full listing: {len(employees)} employees in {len(teams)} teams.\n")
    echo(f"   {'ID':<7} {'name':<15} {'team':<12} office")
    for e in employees:
        echo(f"   {e['id']:<7} {e['name']:<15} {teams[e['team_id']]:<12} {e['office']}")
    offices = Counter(e["office"] for e in employees)
    echo(f"\n   Per office: {', '.join(f'{o} {n}' for o, n in sorted(offices.items()))}.")
    echo(f"   The listing also says as_of = {listing['as_of']}: the cursor for the next poll.")
    echo("   This source pushes nothing. To learn about changes, you have to ask.\n")

    before = call(client, "GET", f"/employees/{MOVED}")
    call(client, "PATCH", f"/employees/{MOVED}", json={"office": "Milan"})
    delta = call(client, "GET", "/employees", params={"updated_since": listing["as_of"]})
    echo(f"2. HR moves {before['name']} ({MOVED}) from {before['office']} to Milan.")
    echo(f"   Poll: GET /employees?updated_since=<as_of> -> {_ids(delta)}")
    echo("   Only the changed record comes back. updated_since is inclusive, so a record")
    echo("   stamped at exactly that instant would come back once more: never missed,")
    echo("   sometimes repeated (T2 drops repeats in S4).\n")

    leaver = call(client, "GET", f"/employees/{LEAVER}")
    call(client, "DELETE", f"/employees/{LEAVER}")
    silent = call(client, "GET", "/employees", params={"updated_since": delta["as_of"]})
    echo(f"3. HR deletes {leaver['name']}'s record ({LEAVER}).")
    echo(f"   Poll since the last as_of -> {_ids(silent)}")
    gone = LEAVER not in {r["id"] for r in silent["records"]}
    echo(f"   {LEAVER} is {'not ' if gone else ''}in the delta: the delete left no trace.")
    after = call(client, "GET", "/employees")
    missing = sorted({e["id"] for e in employees} - {e["id"] for e in after["records"]})
    echo(f"   Compare the first full listing with a new one -> missing: {', '.join(missing)}")
    echo("   That comparison is the only way to see a delete here, which is why the")
    echo("   connector (S2a) runs a full listing on a schedule.\n")

    refused = client.delete(f"/employees/{MANAGER}")
    echo(f"4. HR tries to delete {MANAGER}, who still leads a team and manages people:")
    echo(f"   {refused.status_code}: {refused.json()['detail']}")
    echo("   Record systems enforce rules like this; the mock does too, so the data")
    echo("   stays consistent for the steps that link documents to people (S9).\n")
    echo("Try it yourself: `uv run python -m sources.mock_hr_api`, then open")
    echo("http://127.0.0.1:8082/docs.")


def _ids(record_list: dict[str, list[dict[str, str]]]) -> str:
    ids = [r["id"] for r in record_list["records"]]
    return ", ".join(ids) if ids else "nothing"


@demo_app.command("s1b")
def s1b(
    url: Annotated[
        str | None,
        typer.Option(help="Use a mock HR API that is already running, e.g. http://127.0.0.1:8082."),
    ] = None,
) -> None:
    """S1b · Sources: poll the HR record API for changes; see that deletes are silent."""
    try:
        with mock_client(MODULE, url) as client:
            run_s1b(client)
    except (MockStartError, httpx.HTTPError) as exc:
        raise fail(exc) from exc
