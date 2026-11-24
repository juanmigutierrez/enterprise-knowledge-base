"""S5 · T3 Parse: from a stored original to clean, structured text.

``ekb demo t3`` connects the docs tool (S2) to the change log (S3), runs T2
(S4) and, behind it, T3 on every event T2 sends on. It then follows one page
from its stored bytes to its blocks, shows a table that survives as a table,
parses the same page with the structure-blind baseline, and makes a few
changes to show that only changed content is parsed again.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from contextlib import ExitStack
from typing import Annotated, Any

import httpx
import typer

from ekb.app.changelog import ChangeLog
from ekb.app.demos._app import demo_app
from ekb.app.demos._mocks import Echo, MockStartError, call, fail, mock_client
from ekb.app.demos._serve import serving
from ekb.app.diff import diff_step, run_t2
from ekb.app.parse import DEFAULT_PARSER, DOCUMENT_SOURCES, parse_after_t2, parse_step, parser
from ekb.changelog.bus.port import Delivery
from ekb.connect.docs_api import DocsApi
from ekb.connect.identity_api import IdentityApi
from ekb.connect.options.docs_full_listing import DocsFullListingConnector
from ekb.connect.options.docs_webhook import DocsWebhookConnector
from ekb.connect.people import PersonMap, load_people
from ekb.connect.receiver import DOCS_WEBHOOK_PATH, create_receiver_app
from ekb.contracts import Block, BlockKind, DiffAction, ParsedDocument
from ekb.platform import registry
from ekb.process.t2_diff.step import DiffResult
from ekb.process.t3_parse.step import ParseResult, summary

DOCS, IDP = "sources.mock_docs", "sources.mock_idp"
SOURCE = "hr_docs"
TABLES, EDITED, RESAVED, REVOKED, DELETED = (
    "HR-CMP-01",
    "HR-PL-04",
    "HR-PL-01",
    "HR-PL-05",
    "HR-PL-99",
)
DANA, LUCIA = ({"X-Actor": name} for name in ("dana", "lucia"))
WIDTH = 64


class Story:
    """The demo's wiring: connectors -> change log -> T2 -> T3, and what each produced."""

    def __init__(self, docs: httpx.Client, idp: httpx.Client, echo: Echo, parser_name: str):
        self.docs, self.echo = docs, echo
        self.changelog = ChangeLog.in_memory(partitions=3)
        self.t3 = parse_step(self.changelog, parser_name)
        self.t2 = diff_step(self.changelog, parse_after_t2(self.t3, self._keep, self._forget))
        self.latest: dict[str, ParsedDocument] = {}
        self.decided: list[tuple[DiffResult, ParseResult | None]] = []
        self._pending: ParseResult | None = None
        api = DocsApi(docs)
        people = PersonMap(load_people(IdentityApi(idp)))
        sink, store = self.changelog.publish, self.changelog.store_original
        known = self.changelog.known(SOURCE)
        self.webhook = DocsWebhookConnector(api, sink, store, people, known=known)
        self.listing = DocsFullListingConnector(api, sink, store, people, known=known)

    def _keep(self, result: ParseResult) -> None:
        self._pending = result
        if result.parsed is not None:
            self.latest[result.event.document_key] = result.parsed

    def _forget(self, document_key: str) -> None:
        self.latest.pop(document_key, None)

    def run(self) -> list[tuple[DiffResult, ParseResult | None]]:
        """T2 drains its group; T3 runs on whatever T2 sends on. Pairs each decision with T3's."""
        new: list[tuple[DiffResult, ParseResult | None]] = []

        def seen(_: Delivery, result: DiffResult) -> None:
            new.append((result, self._pending))
            self._pending = None

        run_t2(self.changelog, self.t2, seen)
        self.decided.extend(new)
        return new

    def show(self, new: list[tuple[DiffResult, ParseResult | None]]) -> None:
        for result, parsed in new:
            event, decision = result.decision.event, result.decision
            then = parsed.reason if parsed else _not_run(decision.action)
            self.echo(
                f"   {event.source_id} v{event.version}: T2 {decision.action.value} -> T3 {then}"
            )

    def original(self, source_id: str) -> bytes:
        record = self.changelog.registry.get(f"{SOURCE}:{source_id}")
        if record is None or record.raw_pointer is None:
            raise LookupError(f"{source_id} has no processed original")
        return self.changelog.rawstore.get(record.raw_pointer)

    def parsed(self, source_id: str) -> ParsedDocument:
        return self.latest[f"{SOURCE}:{source_id}"]


def _not_run(action: DiffAction) -> str:
    return {
        DiffAction.SKIP: "not run (same text: same blocks)",
        DiffAction.ACL_ONLY: "not run (only the readers changed: fast lane to T8)",
        DiffAction.PURGE: "not run; its parsed text is forgotten with its originals",
    }.get(action, "not run")


def _cut(text: str, width: int = WIDTH) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def _tag(block: Block) -> str:
    if block.kind is BlockKind.HEADING:
        return f"h{block.level}"
    if block.kind is BlockKind.LIST_ITEM:
        return "  " * (block.level - 1) + block.marker
    return ""


def _trail(section: tuple[str, ...]) -> str:
    return " > ".join(section) or "(top of the page)"


# ------------------------------------------------------------------------ scenes


def run_t3_demo(
    docs: httpx.Client,
    idp: httpx.Client,
    echo: Echo = typer.echo,
    *,
    doc: str = RESAVED,
    parser_name: str = DEFAULT_PARSER,
) -> Story:
    """The whole story against the docs tool. Returns it for inspection."""
    call(docs, "POST", "/reset")
    call(idp, "POST", "/reset")
    story = Story(docs, idp, echo, parser_name)

    echo("S5 · T3 Parse: from a stored original to clean, structured text\n")
    echo("0. T3 runs where T2 decided 'process' (new or changed content). For each event it")
    echo("   reads the original from the raw store (the exact bytes this version had), opens")
    echo("   the source's envelope to find the file, and parses the file into blocks:")
    echo("   headings, paragraphs, list items, tables, code, each with the headings above it.")
    sources = ", ".join(DOCUMENT_SOURCES)
    echo(f"   Document sources: {sources}. HR records, database rows and identity users are")
    echo("   structured records: T3 doesn't parse them, they are queried (D7).\n")

    _initial_load(story)
    if f"{SOURCE}:{doc}" not in story.latest:
        pages = ", ".join(sorted(key.partition(":")[2] for key in story.latest))
        raise LookupError(f"no page {doc!r} in the docs tool (pages: {pages})")
    _one_page(story, doc)
    _table(story)
    _baseline(story, doc)
    with serving(create_receiver_app(story.webhook.receive)) as receiver:
        hook = call(docs, "POST", "/subscriptions", json={"url": receiver.url + DOCS_WEBHOOK_PATH})
        try:
            _changes(story)
        finally:
            call(docs, "DELETE", f"/subscriptions/{hook['id']}")
    _summary(story)
    return story


def _initial_load(story: Story) -> None:
    echo = story.echo
    echo(f"1. Initial load: full listing -> T2 -> T3 ({story.t3.parser_id}).")
    echo(f"   full listing sync() -> {story.listing.sync()} events")
    for _, parsed in story.run():
        if parsed is not None and parsed.parsed is not None:
            doc = parsed.parsed
            echo(f"   {parsed.event.source_id:<10} v{doc.version:<4} {summary(doc)}")
    echo("")


def _one_page(story: Story, doc: str) -> None:
    echo = story.echo
    parsed = story.parsed(doc)
    original = story.original(doc)
    page: dict[str, Any] = json.loads(original)
    echo(f"2. One page, end to end: {doc} v{parsed.version}, '{parsed.title}'.")
    echo(f"   The stored original: {len(original):,} bytes of JSON, as the docs tool sent it.")
    echo(f"   Its fields: {', '.join(page)}.")
    echo(f"   T3 opens it and takes the body ({parsed.media_type}, {parsed.language}):")
    body = str(page.get("body", ""))
    for line in body.splitlines():
        echo(f"   | {line}" if line else "   |")
    echo(f"   Parsed into {summary(parsed)}:")
    for block in parsed.blocks:
        echo(f"   {block.kind.value:<9} {_tag(block):<4} {_cut(block.text)}")
    example = next((b for b in parsed.blocks if b.kind is BlockKind.LIST_ITEM and b.section), None)
    if example is None:
        example = next((b for b in parsed.blocks if b.section), None)
    if example is None:
        echo("   No block on this page has a section with this option (see 4).\n")
        return
    echo(f"   Each block keeps the headings above it. '{_cut(example.text, 40)}'")
    echo(f"   sits under: {_trail(example.section)}. T4 (S6) cuts chunks along these")
    echo("   sections and can name them in each chunk's context header.")
    bold = re.search(r"\*\*(.+?)\*\*", body)
    if bold:
        phrase = bold.group(1)
        echo(f"   Formatting is gone: '**{phrase}**' in the Markdown is '{phrase}' in the text.")
    echo("")


def _table(story: Story) -> None:
    echo = story.echo
    parsed = story.parsed(TABLES)
    tables = [b for b in parsed.blocks if b.kind is BlockKind.TABLE]
    if not tables:
        piped = next((b for b in parsed.blocks if "|" in b.text), None)
        echo(f"3. Tables: {TABLES} '{parsed.title}' has none with this option;")
        echo("   a table is just a paragraph of pipes:")
        if piped is not None:
            for line in piped.text.splitlines()[:4]:
                echo(f"     {line}")
        echo("")
        return
    echo(f"3. Tables survive as tables: {TABLES} '{parsed.title}' has {len(tables)}.")
    for table in tables:
        columns = len(table.rows[0]) if table.rows else 0
        echo(f"   under {_trail(table.section)}: {len(table.rows)} rows x {columns} columns")
    first = tables[0]
    for row in first.rows:
        echo("     " + " | ".join(f"{cell:<8}" for cell in row).rstrip())
    echo("   Each table is one block with its cells: a chunker that keeps it whole never")
    echo("   separates a number from its row and column (E2's maximum stays 'Maximum').\n")


def _baseline(story: Story, doc: str) -> None:
    echo = story.echo
    used = story.parsed(doc)
    file = DOCUMENT_SOURCES[SOURCE](used.document_key, used.version, story.original(doc))
    structured, plain = parser("markdown").parse(file), parser("plain_text").parse(file)
    echo(f"4. The baseline: the same original of {doc}, parsed by both options.")
    echo(f"   markdown    {summary(structured)}")
    echo(f"   plain_text  {summary(plain)}")
    echo("   Plain text's first paragraphs:")
    for block in plain.blocks[:3]:
        lines = block.text.splitlines()
        echo(f"   [ {lines[0]}")
        for line in lines[1:]:
            echo(f"     {line}")
    sections = sum(1 for b in plain.blocks if b.section)
    echo(f"   Headings are just lines of text, glued to the paragraph below; {sections} blocks")
    echo("   know their section; a list is one paragraph. Card 4.1's first experiment")
    echo("   measures what that costs in retrieval (S18).\n")


def _changes(story: Story) -> None:
    echo, docs = story.echo, story.docs
    echo("5. Only new or changed content is parsed again.")
    echo(f"   dana edits {EDITED}; dana saves {RESAVED} with the same text; lucia removes")
    echo(f"   hr-team from {REVOKED}'s readers; dana deletes {DELETED}:")
    call(
        docs,
        "PUT",
        f"/pages/{EDITED}",
        json={"body": "# Remote work\n\n## The rule\nFriday is remote too.\n"},
        headers=DANA,
    )
    page = call(docs, "GET", f"/pages/{RESAVED}")
    call(docs, "PUT", f"/pages/{RESAVED}", json={"body": page["body"]}, headers=DANA)
    call(docs, "PUT", f"/pages/{REVOKED}/acl", json={"groups": ["es-employees"]}, headers=LUCIA)
    call(docs, "DELETE", f"/pages/{DELETED}", headers=DANA)
    story.webhook.sync()
    story.show(story.run())
    gone = f"{SOURCE}:{DELETED}" not in story.latest
    echo(
        f"   {DELETED}'s parsed text is gone with it: {gone}. Parsed documents kept: "
        f"{len(story.latest)}.\n"
    )


def _summary(story: Story) -> None:
    echo = story.echo
    counts = Counter(result.decision.action for result, _ in story.decided)
    line = ", ".join(f"{a.value} {counts[a]}" for a in DiffAction)
    runs = sum(1 for _, parsed in story.decided if parsed is not None)
    echo(f"6. T2 decided {len(story.decided)} events: {line}.")
    echo(f"   T3 ran {runs} times: once per 'process', never for the others.")
    echo("   Next, T4 (S6) cuts these blocks into chunks.")


# -------------------------------------------------------------------------- CLI


@demo_app.command("t3")
def t3(
    doc: Annotated[str, typer.Option(help="The page to follow end to end.")] = RESAVED,
    parser_name: Annotated[
        str, typer.Option("--parser", help="T3 option for the story: markdown or plain_text.")
    ] = DEFAULT_PARSER,
    url: Annotated[
        str | None, typer.Option(help="A running mock docs service, e.g. http://127.0.0.1:8081.")
    ] = None,
    idp_url: Annotated[
        str | None, typer.Option(help="A running mock identity provider (port 8083).")
    ] = None,
) -> None:
    """S5 · T3 Parse: follow a page from its stored original to clean, structured blocks."""
    if parser_name not in registry.options("t3_parse"):
        options = ", ".join(registry.options("t3_parse"))
        raise typer.BadParameter(f"choose one of: {options}", param_hint="--parser")
    try:
        with ExitStack() as stack:
            docs = stack.enter_context(mock_client(DOCS, url))
            idp = stack.enter_context(mock_client(IDP, idp_url))
            run_t3_demo(docs, idp, doc=doc, parser_name=parser_name)
    except (MockStartError, httpx.HTTPError, LookupError) as exc:
        raise fail(exc) from exc
