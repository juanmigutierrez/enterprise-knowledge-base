"""S8 · T4 context header + T5 Classify: what metadata each chunk carries, and why.

``ekb demo t5`` connects the docs tool (S2) to the change log (S3) and runs T2
(S4), T3 (S5), T4 (S6) with its **context header** written through the model
gateway (S7), and T5 on every chunk. It follows one chunk before and after its
header, shows every page's labels and why a restricted page is restricted down
to its last chunk, adds a page holding personal data, replays every page to
show the headers coming from the cache, and makes a few changes: only changed
content gets new headers and labels, and a delete takes its cached replies with
it.

The headers come from your free tiers (keys in ``.env``, see the S7
walkthrough); ``--fake`` runs the same story offline on two fake models, which
write placeholder headers.
"""

from __future__ import annotations

import os
import textwrap
from collections import Counter
from contextlib import ExitStack
from typing import Annotated

import httpx
import typer

from ekb.app.changelog import ChangeLog
from ekb.app.chunk import HEADER_OPTIONS, Processed, chunk_after_t3, chunk_step, header_writer
from ekb.app.classify import CLASSIFIER_OPTIONS, classify_step, forgetter
from ekb.app.demos._app import demo_app
from ekb.app.demos._mocks import Echo, MockStartError, call, fail, mock_client
from ekb.app.demos._serve import serving
from ekb.app.diff import diff_step, run_t2
from ekb.app.gateway import build_gateway, provider_status
from ekb.app.parse import parse_after_t2, parse_step
from ekb.changelog.bus.port import Delivery
from ekb.connect.docs_api import DocsApi
from ekb.connect.identity_api import IdentityApi
from ekb.connect.options.docs_full_listing import DocsFullListingConnector
from ekb.connect.options.docs_webhook import DocsWebhookConnector
from ekb.connect.people import PersonMap, load_people
from ekb.connect.receiver import DOCS_WEBHOOK_PATH, create_receiver_app
from ekb.contracts import Chunk, DiffAction, Sensitivity
from ekb.platform.config import load_settings
from ekb.platform.gateway.config import GatewayConfig, ModelSpec, ProviderSpec, Quota
from ekb.platform.gateway.errors import GatewayError
from ekb.platform.gateway.gateway import ModelGateway
from ekb.platform.gateway.options.cache_memory import InMemoryCache
from ekb.platform.gateway.options.cache_sqlite import SqliteCache
from ekb.platform.gateway.options.log_memory import InMemoryCostLog
from ekb.process.t2_diff.step import DiffResult
from ekb.process.t4_chunk.header import HeaderOutcome
from ekb.process.t5_classify.step import summary as labels_summary

DOCS, IDP = "sources.mock_docs", "sources.mock_idp"
SOURCE = "hr_docs"
HEADED, RESTRICTED, EDITED, RESAVED, REVOKED, DELETED, CREATED = (
    "HR-PL-01",
    "HR-CMP-01",
    "HR-PL-04",
    "HR-PL-01",
    "HR-PL-05",
    "HR-PL-99",
    "HR-PL-11",
)
DANA, LUCIA = ({"X-Actor": name} for name in ("dana", "lucia"))
WIDTH = 88
NEAR_MISSES = ("confidential", "salary", "salario", "stipendio")
"""Topic words that appear in internal pages. The rules ignore them on purpose."""

PII_PAGE = {
    "id": CREATED,
    "title": "Payroll contacts, Italy",
    "language": "en",
    "department": "people",
    "acl": {"groups": ["hr-team"]},
    "body": (
        "# Payroll contacts, Italy\n\n"
        "## Who to ask\n"
        "Write to payroll@example.com or call +39 02 1234 5678 with questions about "
        "your payslip.\n\n"
        "## Example of a corrected payment\n"
        "The March salary of the example employee was sent again to IBAN "
        "IT60 X054 2811 1010 0000 0123 456 after the first transfer failed.\n"
    ),
}
"""Synthetic: the address and number are placeholders, the IBAN is the standard
Italian example IBAN (valid check digits, no real account)."""

FAKE_CONFIG = GatewayConfig(
    providers={"fake": ProviderSpec()},
    quotas={"fake": Quota(per_minute=1000, per_day=100_000)},
    models={"fake:fake-small": ModelSpec(quota="fake"), "fake:fake-large": ModelSpec(quota="fake")},
    tasks={
        "context_header": ("fake:fake-small", "fake:fake-large"),
        "classify": ("fake:fake-small", "fake:fake-large"),
    },
)
"""``--fake``: offline models that fill the schema in (placeholder headers), no keys."""


class Story:
    """The wiring: connectors -> change log -> T2 -> T3 -> T4 (+ header) -> T5."""

    def __init__(
        self,
        docs: httpx.Client,
        idp: httpx.Client,
        echo: Echo,
        gateway: ModelGateway,
        *,
        header: str,
        classifier: str,
    ) -> None:
        self.docs, self.echo, self.gateway = docs, echo, gateway
        self.changelog = ChangeLog.in_memory(partitions=3)
        self.t3 = parse_step(self.changelog)
        self.t4 = chunk_step(header=header_writer(header, gateway))
        self.t5 = classify_step(classifier, gateway)
        self.processed: dict[str, Processed] = {}
        self.forgotten: dict[str, int] = {}
        self.decided: list[tuple[DiffResult, Processed | None]] = []
        self._pending: Processed | None = None
        self.first_record = len(gateway.log.records())
        forget = forgetter(gateway, self._drop)

        def forget_and_count(document_key: str) -> None:
            self.forgotten[document_key] = forget(document_key)

        then = parse_after_t2(
            self.t3, chunk_after_t3(self.t4, self._keep, self.t5), forget_and_count
        )
        self.t2 = diff_step(self.changelog, then)
        api = DocsApi(docs)
        people = PersonMap(load_people(IdentityApi(idp)))
        sink, store = self.changelog.publish, self.changelog.store_original
        known = self.changelog.known(SOURCE)
        self.webhook = DocsWebhookConnector(api, sink, store, people, known=known)
        self.listing = DocsFullListingConnector(api, sink, store, people, known=known)

    def _keep(self, processed: Processed) -> None:
        self._pending = processed
        if processed.parse.parsed is not None and processed.chunks is not None:
            self.processed[processed.parse.event.document_key] = processed

    def _drop(self, document_key: str) -> None:
        self.processed.pop(document_key, None)

    def run(self) -> list[tuple[DiffResult, Processed | None]]:
        """T2 drains its group; T3, T4 and T5 run on whatever T2 sends on."""
        new: list[tuple[DiffResult, Processed | None]] = []

        def seen(_: Delivery, result: DiffResult) -> None:
            new.append((result, self._pending))
            self._pending = None

        run_t2(self.changelog, self.t2, seen)
        self.decided.extend(new)
        return new

    def page(self, source_id: str) -> Processed:
        return self.processed[f"{SOURCE}:{source_id}"]

    def kept(self) -> list[Chunk]:
        return [chunk for processed in self.processed.values() for chunk in processed.final]

    def calls(self) -> tuple[int, int]:
        """Model requests and cache hits since the story started."""
        records = self.gateway.log.records()[self.first_record :]
        hits = sum(1 for record in records if not record.requested)
        return len(records) - hits, hits


def _say(echo: Echo, text: str, indent: str = "   ") -> None:
    for line in textwrap.wrap(text, width=WIDTH, initial_indent=indent, subsequent_indent=indent):
        echo(line)


def _trail(chunk: Chunk) -> str:
    return " > ".join(chunk.section) or "(no section)"


def _first_line(chunk: Chunk, limit: int = 60) -> str:
    body = chunk.text.splitlines()[-1] if chunk.section else chunk.text.splitlines()[0]
    return body if len(body) <= limit else body[: limit - 1] + "…"


def _labels(chunk: Chunk) -> str:
    pii = f", pii: {', '.join(kind.value for kind in chunk.pii)}" if chunk.pii else ""
    return f"{chunk.sensitivity}, {chunk.department or 'no department'}{pii}"


def _then(action: DiffAction, processed: Processed | None, forgotten: int | None) -> str:
    if processed is not None and processed.chunks is not None:
        headers = processed.chunks.headers
        how = headers.outcome.value if headers is not None else "no header"
        chunks = processed.final
        return f"T3, T4 {len(chunks)} chunk(s), headers {how} -> T5 {labels_summary(chunks)}"
    if action is DiffAction.PURGE:
        replies = forgotten or 0
        return f"chunks forgotten, and {replies} cached model repl{'y' if replies == 1 else 'ies'}"
    return {
        DiffAction.SKIP: "T3, T4, T5 not run (same text: same headers and labels)",
        DiffAction.ACL_ONLY: "T3, T4, T5 not run (labels come from the text)",
    }.get(action, "T3, T4, T5 not run")


# ------------------------------------------------------------------------ scenes


def run_t5_demo(
    docs: httpx.Client,
    idp: httpx.Client,
    gateway: ModelGateway,
    echo: Echo = typer.echo,
    *,
    header: str = "model",
    classifier: str = "rules",
) -> Story:
    """The whole story against the docs tool. Returns it for inspection."""
    call(docs, "POST", "/reset")
    call(idp, "POST", "/reset")
    story = Story(docs, idp, echo, gateway, header=header, classifier=classifier)

    echo("S8 · T4 context header + T5 Classify: what metadata each chunk carries\n")
    _say(
        echo,
        "0. After T4 cuts a document into chunks, it now writes each chunk a context header "
        "(one call per document, through the model gateway), and T5 labels every chunk "
        "with a department, a sensitivity and the personal data it holds. The access layer "
        "filters on those labels later; the header helps search find the chunk.",
        indent="",
    )
    echo(f"   Header: {story.t4.header_id}. Classifier: {story.t5.classifier_id}.\n")

    _initial_load(story)
    _one_chunk(story)
    _labels_per_page(story)
    _near_misses(story)
    with serving(create_receiver_app(story.webhook.receive)) as receiver:
        hook = call(docs, "POST", "/subscriptions", json={"url": receiver.url + DOCS_WEBHOOK_PATH})
        try:
            _personal_data(story)
            _replay(story)
            _changes(story)
        finally:
            call(docs, "DELETE", f"/subscriptions/{hook['id']}")
    _summary(story)
    return story


def _initial_load(story: Story) -> None:
    echo = story.echo
    echo("1. Initial load: full listing -> T2 -> T3 -> T4 (chunks + headers) -> T5.")
    echo(f"   full listing sync() -> {story.listing.sync()} events")
    for _, processed in story.run():
        if processed is None or processed.chunks is None:
            continue
        chunks, headers = processed.final, processed.chunks.headers
        how = headers.outcome.value if headers is not None else "none"
        echo(
            f"   {processed.parse.event.source_id:<10} {len(chunks)} chunks  headers {how:<7}  "
            f"{labels_summary(chunks)}"
        )
    kept = story.kept()
    headed = sum(1 for chunk in kept if chunk.context_header)
    requests, hits = story.calls()
    echo(f"   {len(kept)} chunks from {len(story.processed)} pages; {headed} have a header.")
    echo(f"   Model calls so far: {requests} requests, {hits} from the cache.\n")


def _one_chunk(story: Story) -> None:
    echo = story.echo
    processed = story.page(HEADED)
    title = processed.chunks.document.title if processed.chunks is not None else ""
    title_words = title.lower()
    nameless = [c for c in processed.final if title_words not in c.text.lower() and c.section]
    chunk = nameless[-1] if nameless else processed.final[-1]
    echo(f"2. One chunk, before and after its header: {chunk.chunk_id}.")
    echo(f"   text:    {_first_line(chunk, 70)}")
    echo(f"   section: {_trail(chunk)}")
    _say(echo, f"It never says '{title}'. A search for the policy has little to match.")
    headers = processed.chunks.headers if processed.chunks is not None else None
    if chunk.context_header:
        echo(f"   header:  {chunk.context_header}")
        echo(f"   by:      {chunk.header_id}")
        _say(
            echo,
            "The header is kept apart from the text: from S10 it is put in front of the text "
            "when the chunk is embedded and indexed, never quoted in an answer.",
        )
    else:
        note = headers.note if headers is not None else "no header writer"
        _say(echo, f"header:  '' ({note})")
        if headers is not None and headers.outcome is HeaderOutcome.FAILED:
            _say(
                echo,
                "Set a free API key (docs/walkthrough/S07-model-gateway.md) and run with "
                "`uv run --env-file .env ekb demo t5`, or run offline with --fake.",
            )
    echo("")


def _labels_per_page(story: Story) -> None:
    echo = story.echo
    echo("3. Labels, page by page (department, and chunks per sensitivity):")
    for key, processed in sorted(story.processed.items()):
        chunks = processed.final
        labels = processed.labels
        department = chunks[0].department if chunks else None
        origin = "source" if labels is not None and "from the source" in labels.note else "rules"
        echo(
            f"   {key.partition(':')[2]:<10} {department or '-':<8} ({origin})  "
            f"{labels_summary(chunks)}"
        )
    processed = story.page(RESTRICTED)
    labels = processed.labels
    echo(f"   {RESTRICTED}, chunk by chunk:")
    for chunk, why in zip(processed.final, labels.why if labels else (), strict=False):
        echo(f"   {chunk.chunk_id}  {_first_line(chunk, 50)}")
        echo(f"      {why}")
    _say(
        echo,
        "The restriction is written once, at the top. Every chunk below it is restricted "
        "too, the review cycle included, which never says so itself: a missed restriction "
        "is a leak, an extra one only friction (card 4.9).",
    )
    echo("")


def _near_misses(story: Story) -> None:
    echo = story.echo
    echo("4. Near misses: topic words are not markers.")
    shown = 0
    for chunk in story.kept():
        if chunk.sensitivity is not Sensitivity.INTERNAL:
            continue
        word = next((w for w in NEAR_MISSES if w in chunk.text.lower()), None)
        if word is not None and shown < 3:
            echo(f"   {chunk.chunk_id}  says '{word}' -> {chunk.sensitivity}")
            shown += 1
    _say(
        echo,
        "The code of conduct says shopper data 'are confidential'; the remote work policy "
        "pays an allowance 'with the salary'. Neither page is confidential, so the rules "
        "look for markers ('Restricted to', 'Do not forward', '(confidential)'), not words.",
    )
    echo("")


def _personal_data(story: Story) -> None:
    echo = story.echo
    echo(f"5. Personal data. dana creates {CREATED} '{PII_PAGE['title']}' (synthetic):")
    call(story.docs, "POST", "/pages", json=PII_PAGE, headers=DANA)
    story.webhook.sync()
    story.run()
    processed = story.page(CREATED)
    for chunk, why in zip(
        processed.final, processed.labels.why if processed.labels else (), strict=False
    ):
        echo(f"   {chunk.chunk_id}  {_labels(chunk)}")
        echo(f"      {why}")
    _say(
        echo,
        "An email address and a phone number are flagged but don't raise a chunk: a policy "
        "giving the payroll team's address is not confidential. An IBAN identifies a "
        "person's money, so its chunk alone becomes confidential.",
    )
    echo("")


def _replay(story: Story) -> None:
    echo = story.echo
    before = story.calls()
    for processed in story.processed.values():
        if processed.parse.parsed is not None:
            story.t4.handle(processed.parse.parsed)
    requests, hits = (now - then for now, then in zip(story.calls(), before, strict=True))
    echo(f"6. A replay: T4 again on all {len(story.processed)} pages, as a rebuild would.")
    if requests + hits == 0:
        why = (
            "this header option writes none"
            if story.t4.header_id.startswith("none")
            else ("no model is available, so nothing was cached")
        )
        echo(f"   No model calls: {why}.\n")
        return
    echo(f"   {requests} requests, {hits} from the cache.")
    _say(
        echo,
        "The cache key holds the document's text (D16 #1): an unchanged page costs nothing "
        "the second time, today or after a restart. An edited page gets a new key.",
    )
    echo("")


def _changes(story: Story) -> None:
    echo, docs = story.echo, story.docs
    echo("7. Only changed content gets new headers and labels; a delete takes its replies.")
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
        then = _then(action, processed, story.forgotten.get(event.document_key))
        echo(f"   {event.source_id} v{event.version}: T2 {action.value} -> {then}")
    echo("")


def _summary(story: Story) -> None:
    echo = story.echo
    counts = Counter(result.decision.action for result, _ in story.decided)
    line = ", ".join(f"{a.value} {counts[a]}" for a in DiffAction)
    runs = sum(1 for _, p in story.decided if p is not None and p.labels is not None)
    kept = story.kept()
    requests, hits = story.calls()
    publishable = sum(1 for chunk in kept if chunk.publishable)
    labelled = sum(1 for chunk in kept if chunk.sensitivity is not None)
    echo(f"8. T2 decided {len(story.decided)} events: {line}.")
    echo(f"   T4's header and T5 ran {runs} times: once per 'process', never for the others.")
    echo(f"   Model calls in this run: {requests} requests, {hits} from the cache.")
    echo(f"   Labelled chunks: {labelled} of {len(kept)}.")
    echo(f"   Publishable chunks: {publishable} of {len(kept)}: each has a sensitivity now,")
    echo("   but no vector (T7) or ACL (T8) yet, so none could be searched.")


# -------------------------------------------------------------------------- CLI


def close_cache(gateway: ModelGateway) -> None:
    """Close a file-backed cache when the demo ends (Windows cannot delete an open file)."""
    if isinstance(gateway.cache, SqliteCache):
        gateway.cache.close()


def demo_gateway(fake: bool) -> tuple[ModelGateway, str | None]:
    """The gateway for the story, and a warning when no model provider has a key."""
    if fake:
        return build_gateway(FAKE_CONFIG, cache=InMemoryCache(), log=InMemoryCostLog()), None
    config = load_settings().gateway
    ready = any(p.ready for p in provider_status(config, os.environ, kind="chat"))
    warning = None if ready else "no model provider has a key: headers will be empty"
    return build_gateway(config), warning


@demo_app.command("t5")
def t5(
    fake: Annotated[
        bool, typer.Option("--fake", help="Offline: two fake models write placeholder headers.")
    ] = False,
    header: Annotated[
        str, typer.Option(help="T4's header option: model (through the gateway) or none.")
    ] = "model",
    classifier: Annotated[
        str, typer.Option(help="T5's option: rules or model_plus_rules.")
    ] = "rules",
    url: Annotated[
        str | None, typer.Option(help="A running mock docs service, e.g. http://127.0.0.1:8081.")
    ] = None,
    idp_url: Annotated[
        str | None, typer.Option(help="A running mock identity provider (port 8083).")
    ] = None,
) -> None:
    """S8 · T4 header + T5 Classify: headers through the gateway, labels and why."""
    if header not in HEADER_OPTIONS:
        raise typer.BadParameter(
            f"choose one of: {', '.join(HEADER_OPTIONS)}", param_hint="--header"
        )
    if classifier not in CLASSIFIER_OPTIONS:
        raise typer.BadParameter(
            f"choose one of: {', '.join(CLASSIFIER_OPTIONS)}", param_hint="--classifier"
        )
    try:
        gateway, warning = demo_gateway(fake)
        if warning is not None and header == "model":
            typer.secho(f"Note: {warning}; set keys in .env or use --fake.\n", fg="yellow")
        with ExitStack() as stack:
            stack.callback(close_cache, gateway)
            docs = stack.enter_context(mock_client(DOCS, url))
            idp = stack.enter_context(mock_client(IDP, idp_url))
            run_t5_demo(docs, idp, gateway, header=header, classifier=classifier)
    except (MockStartError, httpx.HTTPError, LookupError, GatewayError, ValueError) as exc:
        raise fail(exc) from exc
