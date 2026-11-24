"""S6 · T4 Chunk: from parsed blocks to the passages that will be searched.

``ekb demo t4`` connects the docs tool (S2) to the change log (S3), runs T2
(S4), T3 (S5) and, behind them, T4 on every event T2 sends on. It then
follows one policy chunk by chunk, cuts the same pages with the fixed-window
baseline to show what a bad boundary does to a table, points at a chunk that
needs the context header S8 will add, and makes a few changes to show that
only changed content is chunked again and that a delete takes its chunks.
"""

from __future__ import annotations

import textwrap
from collections import Counter
from contextlib import ExitStack
from typing import Annotated

import httpx
import typer

from ekb.app.changelog import ChangeLog
from ekb.app.chunk import DEFAULT_CHUNKER, Processed, chunk_after_t3, chunk_step, chunker
from ekb.app.demos._app import demo_app
from ekb.app.demos._mocks import Echo, MockStartError, call, fail, mock_client
from ekb.app.demos._serve import serving
from ekb.app.diff import diff_step, run_t2
from ekb.app.parse import parse_after_t2, parse_step
from ekb.changelog.bus.port import Delivery
from ekb.connect.docs_api import DocsApi
from ekb.connect.identity_api import IdentityApi
from ekb.connect.options.docs_full_listing import DocsFullListingConnector
from ekb.connect.options.docs_webhook import DocsWebhookConnector
from ekb.connect.people import PersonMap, load_people
from ekb.connect.receiver import DOCS_WEBHOOK_PATH, create_receiver_app
from ekb.contracts import BlockKind, Chunk, DiffAction, ParsedDocument
from ekb.platform import registry
from ekb.process.t2_diff.step import DiffResult
from ekb.process.t3_parse.step import summary as parse_summary
from ekb.process.t4_chunk.step import ChunkResult, ChunkStep, summary

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
WIDTH = 88
SMALL_WINDOW, SMALL_OVERLAP = 60, 10
"""Scene 3 only: our pages are shorter than 400 tokens, so the baseline is
shrunk to make its cuts visible."""


class Story:
    """The demo's wiring: connectors -> change log -> T2 -> T3 -> T4, and what each produced."""

    def __init__(self, docs: httpx.Client, idp: httpx.Client, echo: Echo, t4: ChunkStep):
        self.docs, self.echo = docs, echo
        self.changelog = ChangeLog.in_memory(partitions=3)
        self.t3 = parse_step(self.changelog)
        self.t4 = t4
        then = parse_after_t2(self.t3, chunk_after_t3(t4, self._keep), self._forget)
        self.t2 = diff_step(self.changelog, then)
        self.parsed: dict[str, ParsedDocument] = {}
        self.chunks: dict[str, ChunkResult] = {}
        self.decided: list[tuple[DiffResult, Processed | None]] = []
        self._pending: Processed | None = None
        api = DocsApi(docs)
        people = PersonMap(load_people(IdentityApi(idp)))
        sink, store = self.changelog.publish, self.changelog.store_original
        known = self.changelog.known(SOURCE)
        self.webhook = DocsWebhookConnector(api, sink, store, people, known=known)
        self.listing = DocsFullListingConnector(api, sink, store, people, known=known)

    def _keep(self, processed: Processed) -> None:
        self._pending = processed
        key = processed.parse.event.document_key
        if processed.parse.parsed is not None and processed.chunks is not None:
            self.parsed[key] = processed.parse.parsed
            self.chunks[key] = processed.chunks

    def _forget(self, document_key: str) -> None:
        self.parsed.pop(document_key, None)
        self.chunks.pop(document_key, None)

    def run(self) -> list[tuple[DiffResult, Processed | None]]:
        """T2 drains its group; T3 and T4 run on whatever T2 sends on."""
        new: list[tuple[DiffResult, Processed | None]] = []

        def seen(_: Delivery, result: DiffResult) -> None:
            new.append((result, self._pending))
            self._pending = None

        run_t2(self.changelog, self.t2, seen)
        self.decided.extend(new)
        return new

    def page(self, source_id: str) -> tuple[ParsedDocument, ChunkResult]:
        key = f"{SOURCE}:{source_id}"
        return self.parsed[key], self.chunks[key]

    def kept(self) -> list[Chunk]:
        return [chunk for result in self.chunks.values() for chunk in result.chunks]


def _say(echo: Echo, text: str) -> None:
    """Prose, wrapped like the other demos' lines."""
    for line in textwrap.wrap(text, width=WIDTH, initial_indent="   ", subsequent_indent="   "):
        echo(line)


def _trail(section: tuple[str, ...]) -> str:
    return " > ".join(section) or "(no section)"


def _show_chunk(echo: Echo, chunk: Chunk) -> None:
    echo(f"   {chunk.chunk_id}  {chunk.tokens} tokens  § {_trail(chunk.section)}")
    for line in chunk.text.splitlines():
        echo(f"   | {line}" if line else "   |")


def _then(action: DiffAction, processed: Processed | None) -> str:
    if processed is not None and processed.parse.parsed is not None and processed.chunks:
        chunks = processed.chunks
        return f"T3 {parse_summary(processed.parse.parsed)} -> T4 {summary(chunks.chunks)}"
    return {
        DiffAction.SKIP: "T3, T4 not run (same text: same chunks)",
        DiffAction.ACL_ONLY: "T3, T4 not run (only the readers changed: fast lane to T8)",
        DiffAction.PURGE: "T3, T4 not run; its chunks are forgotten with its parsed text",
    }.get(action, "T3, T4 not run")


# ------------------------------------------------------------------------ scenes


def run_t4_demo(
    docs: httpx.Client,
    idp: httpx.Client,
    echo: Echo = typer.echo,
    *,
    doc: str = RESAVED,
    chunker_name: str = DEFAULT_CHUNKER,
    max_tokens: int = 400,
) -> Story:
    """The whole story against the docs tool. Returns it for inspection."""
    t4 = chunk_step(chunker_name, max_tokens=max_tokens)  # bad settings fail before any reset
    call(docs, "POST", "/reset")
    call(idp, "POST", "/reset")
    story = Story(docs, idp, echo, t4)

    echo("S6 · T4 Chunk: from parsed blocks to the passages that will be searched\n")
    echo("0. T4 runs right after T3, so only where T2 decided 'process'. It cuts each parsed")
    echo("   document into chunks: the passages that will be labelled (T5), linked to")
    echo("   entities (T6), embedded (T7), given permissions (T8) and searched. Where the")
    echo("   cuts fall decides what a search can find.\n")

    _initial_load(story)
    if f"{SOURCE}:{doc}" not in story.chunks:
        pages = ", ".join(sorted(key.partition(":")[2] for key in story.chunks))
        raise LookupError(f"no page {doc!r} in the docs tool (pages: {pages})")
    _one_page(story, doc)
    _boundaries(story)
    _header(story, doc)
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
    echo(f"1. Initial load: full listing -> T2 -> T3 -> T4 ({story.t4.chunker_id}).")
    echo(f"   full listing sync() -> {story.listing.sync()} events")
    for _, processed in story.run():
        if processed is not None and processed.parse.parsed is not None and processed.chunks:
            parsed, chunks = processed.parse.parsed, processed.chunks
            blocks = f"{len(parsed.blocks)} blocks"
            echo(
                f"   {processed.parse.event.source_id:<10} v{parsed.version:<4} {blocks:>9} -> "
                f"{summary(chunks.chunks, chunks.max_tokens)}"
            )
    kept = story.kept()
    over = sum(result.over_limit for result in story.chunks.values())
    echo(
        f"   {len(kept)} chunks from {len(story.chunks)} pages; {over} over the "
        f"{story.t4.max_tokens}-token limit.\n"
    )


def _one_page(story: Story, doc: str) -> None:
    echo = story.echo
    parsed, result = story.page(doc)
    echo(f"2. One policy, chunk by chunk: {doc} v{parsed.version}, '{parsed.title}'.")
    for chunk in result.chunks:
        _show_chunk(echo, chunk)
    firsts = {chunk.text.splitlines()[0] for chunk in result.chunks}
    alone = [b.text for b in parsed.blocks if b.kind is BlockKind.HEADING and b.text not in firsts]
    if result.chunks and result.chunks[0].section:
        echo("   Each chunk stays within one section. The first chunk includes its heading.")
        echo("   Continuation chunks carry the same heading trail as metadata.")
        if alone:
            names = ", ".join(f"'{name}'" for name in alone)
            echo(f"   {names} has no text of its own: no chunk, but it is in every")
            echo("   chunk's trail.")
    echo("")


def _boundaries(story: Story) -> None:
    echo = story.echo
    whole = chunker("fixed")
    pages = [whole.chunk(parsed) for parsed in story.parsed.values()]
    sizes = [chunks[0].tokens for chunks in pages if len(chunks) == 1]
    echo("3. Why boundaries matter: the same pages, cut by the fixed-window baseline.")
    echo(f"   {whole.chunker_id}: {sum(len(c) for c in pages)} chunks for {len(pages)} pages.")
    if len(sizes) == len(pages):
        _say(
            echo,
            f"Our pages are {min(sizes)}-{max(sizes)} tokens, so at 400 nothing is cut: each "
            "page is one chunk, every topic of the policy in one passage.",
        )
    parsed, _ = story.page(TABLES)
    windows = chunker("fixed", max_tokens=SMALL_WINDOW, overlap_tokens=SMALL_OVERLAP).chunk(parsed)
    _say(
        echo,
        f"Shrunk to {SMALL_WINDOW} tokens (overlap {SMALL_OVERLAP}), only to make the cuts "
        f"visible, on {TABLES} '{parsed.title}':",
    )
    for window in windows:
        _show_chunk(echo, window)
    for fact in _cut_tables(parsed, windows):
        _say(echo, fact)
    aware = chunker("structure_aware")
    table = next((c for c in aware.chunk(parsed) if " | " in c.text), None)
    if table is not None:
        echo(f"   {aware.chunker_id} keeps each table whole, under its heading:")
        _show_chunk(echo, table)
    echo("")


def _cut_tables(parsed: ParsedDocument, windows: tuple[Chunk, ...]) -> list[str]:
    """Plain facts about where the windows cut the document's lines and tables.

    Found by position in the document's text, not by matching lines: both
    salary tables have the same header row, so "the window has a header row"
    says nothing about *which* table's header it is.
    """
    text = parsed.text
    starts: list[int] = []
    at = 0
    for window in windows:
        at = text.find(window.text, at)
        if at < 0:
            return []
        starts.append(at)
        at += 1
    line_starts = {0} | {i + 1 for i, char in enumerate(text) if char == "\n"}
    facts = [
        f"{window.chunk_id} starts in the middle of a line: '{window.text.splitlines()[0]}'."
        for window, start in zip(windows, starts, strict=True)
        if start not in line_starts
    ]
    for block in parsed.blocks:
        if block.kind is not BlockKind.TABLE:
            continue
        table_at = text.find(block.text)
        header, *rows = block.text.splitlines()
        spans, offset = [], table_at + len(header) + 1
        for row in rows:
            spans.append((offset, offset + len(row)))
            offset += len(row) + 1
        for window, start in zip(windows, starts, strict=True):
            if start <= table_at:
                continue  # this window has the table's start: its heading and header row
            end = start + len(window.text)
            cut = [row for row, (a, b) in zip(rows, spans, strict=True) if start <= a and b <= end]
            if cut:
                name = block.section[-1] if block.section else "the page's"
                facts.append(
                    f"{window.chunk_id} holds {len(cut)} row(s) of the '{name}' table without "
                    f"its heading or header row: '{cut[0]}' no longer says which table it is "
                    "from or which number is the maximum."
                )
    return facts


def _header(story: Story, doc: str) -> None:
    echo = story.echo
    parsed, result = story.page(doc)
    title = parsed.title.lower()
    nameless = [c for c in result.chunks if title not in c.text.lower()]
    echo("4. Why headers matter (S8).")
    if not nameless or not nameless[-1].section:
        echo("   With this chunker the chunks carry no section, so there is nothing for")
        echo("   a header to say beyond the page title.\n")
        return
    chunk = nameless[-1]
    body = chunk.text.splitlines()[-1]
    echo(f"   {chunk.chunk_id} reads: '{body[:70]}{'…' if len(body) > 70 else ''}'")
    _say(echo, f"It never says '{parsed.title}'. Its trail does: {_trail(chunk.section)}.")
    _say(
        echo,
        f"Its context header is {chunk.context_header!r} for now. In S8 a model writes one "
        "through the gateway (S7), so the chunk is found by questions about the policy it "
        "belongs to; card 4.3 measures what that adds.",
    )
    echo("")


def _changes(story: Story) -> None:
    echo, docs = story.echo, story.docs
    echo("5. Only new or changed content is chunked again; a delete takes its chunks.")
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
    for result, processed in story.run():
        event, action = result.decision.event, result.decision.action
        then = _then(action, processed)
        echo(f"   {event.source_id} v{event.version}: T2 {action.value} -> {then}")
    gone = f"{SOURCE}:{DELETED}" not in story.chunks
    echo(
        f"   {DELETED}'s chunks are gone with it: {gone}. Chunks kept: {len(story.kept())} "
        f"from {len(story.chunks)} pages.\n"
    )


def _summary(story: Story) -> None:
    echo = story.echo
    counts = Counter(result.decision.action for result, _ in story.decided)
    line = ", ".join(f"{a.value} {counts[a]}" for a in DiffAction)
    runs = sum(1 for _, p in story.decided if p is not None and p.chunks is not None)
    kept = story.kept()
    publishable = sum(1 for chunk in kept if chunk.publishable)
    echo(f"6. T2 decided {len(story.decided)} events: {line}.")
    echo(f"   T3 and T4 ran {runs} times: once per 'process', never for the others.")
    echo(f"   Publishable chunks: {publishable} of {len(kept)}. None has a sensitivity (T5),")
    echo("   a vector (T7) or an ACL (T8) yet, so none could be searched.")
    echo("   Next, the model gateway (S7), then the context header (S8).")


# -------------------------------------------------------------------------- CLI


@demo_app.command("t4")
def t4(
    doc: Annotated[str, typer.Option(help="The page to follow chunk by chunk.")] = RESAVED,
    chunker_name: Annotated[
        str,
        typer.Option("--chunker", help="T4 option for the story: structure_aware or fixed."),
    ] = DEFAULT_CHUNKER,
    max_tokens: Annotated[
        int, typer.Option(min=1, help="The chunker's size limit, in approximate tokens.")
    ] = 400,
    url: Annotated[
        str | None, typer.Option(help="A running mock docs service, e.g. http://127.0.0.1:8081.")
    ] = None,
    idp_url: Annotated[
        str | None, typer.Option(help="A running mock identity provider (port 8083).")
    ] = None,
) -> None:
    """S6 · T4 Chunk: follow a policy from parsed blocks to chunks, and compare the baseline."""
    if chunker_name not in registry.options("t4_chunk"):
        options = ", ".join(registry.options("t4_chunk"))
        raise typer.BadParameter(f"choose one of: {options}", param_hint="--chunker")
    try:
        chunker(chunker_name, max_tokens=max_tokens)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--max-tokens") from exc
    try:
        with ExitStack() as stack:
            docs = stack.enter_context(mock_client(DOCS, url))
            idp = stack.enter_context(mock_client(IDP, idp_url))
            run_t4_demo(docs, idp, doc=doc, chunker_name=chunker_name, max_tokens=max_tokens)
    except (MockStartError, httpx.HTTPError, LookupError) as exc:
        raise fail(exc) from exc
