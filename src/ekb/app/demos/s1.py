"""S1 · Sources: the mock docs tool, and what "a change" is."""

from __future__ import annotations

from typing import Annotated

import httpx
import typer

from ekb.app.demos._app import demo_app
from ekb.app.demos._mocks import Echo, MockStartError, call, fail, mock_client

MODULE = "sources.mock_docs"
EDITED_PAGE = "HR-PL-04"  # Remote work, Milan: a policy that changes in real life
ACL_PAGE = "HR-PL-05"  # Teletrabajo, Madrid: visible to Spanish staff and HR


def _readers(acl: dict[str, list[str]]) -> str:
    names = [*acl.get("groups", []), *(f"user:{u}" for u in acl.get("users", []))]
    return ", ".join(names) or "nobody"


def _short(content_hash: str) -> str:
    return content_hash.removeprefix("sha256:")[:10]


def run_s1(client: httpx.Client, echo: Echo = typer.echo) -> None:
    """The S1 story against a running mock docs service (any ``httpx.Client``)."""
    call(client, "POST", "/reset")

    echo("S1 · The mock HR docs source (stands in for SharePoint / Confluence)\n")
    pages = call(client, "GET", "/pages")
    echo(f"1. The source holds {len(pages)} synthetic HR documents:\n")
    echo(f"   {'ID':<10} {'lang':<4} {'rev':>3}  {'who may read it':<28} title")
    for page in pages:
        readers = _readers(page["acl"])
        echo(
            f"   {page['id']:<10} {page['language']:<4} {page['rev']:>3}  "
            f"{readers:<28} {page['title']}"
        )
    languages = sorted({p["language"] for p in pages})
    restricted = [p["id"] for p in pages if p["acl"]["groups"] == ["hr-compensation"]]
    echo(f"\n   Languages: {', '.join(languages)}.")
    echo(f"   Restricted to hr-compensation: {', '.join(restricted)}.")
    echo("   The listing has versions and hashes but no text: it is how a connector")
    echo("   notices deletes in sources that send no webhooks.\n")

    cursor = call(client, "GET", "/changes")["cursor"]

    before = call(client, "GET", f"/pages/{EDITED_PAGE}")
    new_body = (
        before["body"] + "\nFrom 1 February, Friday is also a remote day for the Milan office.\n"
    )
    after = call(client, "PUT", f"/pages/{EDITED_PAGE}", json={"body": new_body})
    echo(f"2. Someone edits {EDITED_PAGE} ({before['title']}):")
    echo(
        f"   rev {before['rev']} -> {after['rev']}, "
        f"hash {_short(before['content_hash'])} -> {_short(after['content_hash'])}"
    )
    echo("   New text, so a new revision and a new hash.\n")

    again = call(client, "PUT", f"/pages/{EDITED_PAGE}", json={"body": new_body})
    echo(f"3. Someone opens {EDITED_PAGE} and saves it without changing anything:")
    echo(
        f"   rev {after['rev']} -> {again['rev']}, hash {_short(again['content_hash'])} (unchanged)"
    )
    echo("   The source still reports a change. Spotting that nothing really changed")
    echo("   is the system's job (T2 compares hashes, plan step S4).\n")

    acl_before = call(client, "GET", f"/pages/{ACL_PAGE}")
    new_acl = {"groups": [*acl_before["acl"]["groups"], "it-employees"]}
    acl_after = call(client, "PUT", f"/pages/{ACL_PAGE}/acl", json=new_acl)
    echo(f"4. The People team shares {ACL_PAGE} ({acl_before['title']}) with it-employees:")
    echo(f"   readers {_readers(acl_before['acl'])} -> {_readers(acl_after['acl'])}")
    echo(
        f"   acl_rev {acl_before['acl_rev']} -> {acl_after['acl_rev']}, "
        f"rev {acl_before['rev']} -> {acl_after['rev']} (content untouched)"
    )
    echo("   Only permissions changed, so later steps can update access without")
    echo("   re-reading or re-embedding the text (the T2 fast lane).\n")

    feed = call(client, "GET", "/changes", params={"since": cursor})
    echo(f"5. The change feed since cursor {cursor} (what webhooks would carry):")
    for change in feed["changes"]:
        echo(
            f"   #{change['seq']:<3} {change['event']:<26} {change['page_id']:<10} "
            f"rev={change['rev']} acl_rev={change['acl_rev']}"
        )
    echo("   Each entry says *that* a page changed, not its content. In S2 the")
    echo("   connector turns these into the product's ChangeEvent (T1).\n")
    echo("Try it yourself: start the mock with `uv run python -m sources.mock_docs`")
    echo("and open http://127.0.0.1:8081/docs to edit, delete or re-share a page.")


@demo_app.command("s1")
def s1(
    url: Annotated[
        str | None,
        typer.Option(
            help="Use a mock docs service that is already running, e.g. http://127.0.0.1:8081."
        ),
    ] = None,
) -> None:
    """S1 · Sources: list the mock HR documents, edit one, see its version change."""
    try:
        with mock_client(MODULE, url) as client:
            run_s1(client)
    except (MockStartError, httpx.HTTPError) as exc:
        raise fail(exc) from exc
