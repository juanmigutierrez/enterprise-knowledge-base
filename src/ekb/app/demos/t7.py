"""S10 · T7 Embed: what a vector is and why the model ID is stored.

``ekb demo t7`` first embeds five short sentences, the same question in three
languages and two that are about something else, and prints how close each pair
is. Then it connects the docs tool (S2) to the change log (S3) and runs T2 to T7
on every page (S4 to S9, no model but the embedding one: no context header, the
``rules`` classifier), follows one chunk to its vector, edits a page (only the
changed chunk is sent to the model), changes a page's readers (nothing is
embedded) and deletes a page (its cached vectors are forgotten). Last, it shows
what the stored model ID is for.

The model is bge-m3 on Ollama (``config/default.yaml``). When Ollama cannot
answer, or with ``--fake``, it runs on a fake embedding model: the same
plumbing, but numbers with no meaning, and it says so.
"""

from __future__ import annotations

import textwrap
from collections import Counter
from contextlib import ExitStack
from typing import Annotated

import httpx
import typer

from ekb.app.changelog import ChangeLog
from ekb.app.chunk import Processed, chunk_after_t3, chunk_step
from ekb.app.classify import classify_step, forgetter
from ekb.app.demos._app import demo_app
from ekb.app.demos._mocks import Echo, MockStartError, call, fail, mock_client
from ekb.app.demos._serve import serving
from ekb.app.diff import diff_step, run_t2
from ekb.app.embed import embed_help, embedder, probe
from ekb.app.gateway import build_gateway
from ekb.app.parse import parse_after_t2, parse_step
from ekb.changelog.bus.port import Delivery
from ekb.connect.docs_api import DocsApi
from ekb.connect.identity_api import IdentityApi
from ekb.connect.options.docs_full_listing import DocsFullListingConnector
from ekb.connect.options.docs_webhook import DocsWebhookConnector
from ekb.connect.people import PersonMap, load_people
from ekb.connect.receiver import DOCS_WEBHOOK_PATH, create_receiver_app
from ekb.contracts import Chunk, DiffAction
from ekb.platform.config import load_settings
from ekb.platform.gateway.config import GatewayConfig, ModelSpec, ProviderSpec, Quota
from ekb.platform.gateway.errors import GatewayError
from ekb.platform.gateway.gateway import ModelGateway
from ekb.platform.gateway.options.cache_memory import InMemoryCache
from ekb.platform.gateway.options.log_memory import InMemoryCostLog
from ekb.process.t2_diff.step import DiffResult
from ekb.process.t7_embed.input import embed_input
from ekb.process.t7_embed.options.model import model_id as model_id_of
from ekb.process.t7_embed.step import EmbedStep, VectorState, cosine, vector_state

DOCS, IDP = "sources.mock_docs", "sources.mock_idp"
SOURCE = "hr_docs"
FOLLOWED, FOLLOWED_SECTION = "HR-PL-01", "Carrying leave over"
EDITED, REVOKED, DELETED = "HR-PL-01", "HR-PL-05", "HR-PL-99"
DANA, LUCIA = ({"X-Actor": name} for name in ("dana", "lucia"))
WIDTH = 88
PRODUCTION = "bedrock:cohere.embed-v4:0"
"""The production design's model (D10, ``config/profiles/bedrock.yaml``)."""

SENTENCES = (
    ("A", "en", "How many unused vacation days can I carry over to next year?"),
    ("B", "it", "Quanti giorni di ferie non goduti posso riportare all'anno prossimo?"),
    ("C", "es", "¿Cuántos días de vacaciones no usados puedo pasar al año siguiente?"),
    ("D", "en", "Expense claims must be submitted within 30 days of the trip."),
    ("E", "en", "The Milan office closes at 18:00 on Fridays."),
)
"""A, B and C ask the same thing in three languages; D and E are about something else."""

FAKE_CONFIG = GatewayConfig(
    providers={"fake": ProviderSpec()},
    quotas={"fake": Quota(per_minute=1000, per_day=100_000)},
    models={"fake:fake-embed": ModelSpec(quota="fake", kind="embedding", dimensions=1024)},
    tasks={"embed": ("fake:fake-embed",)},
)
"""``--fake``, or no Ollama: an offline embedding model with no meaning, through the gateway."""


class Story:
    """The wiring: connectors -> change log -> T2 -> T3 -> T4 -> T5 -> T7, no chat model."""

    def __init__(self, docs: httpx.Client, idp: httpx.Client, echo: Echo, gateway: ModelGateway):
        self.docs, self.echo, self.gateway = docs, echo, gateway
        self.changelog = ChangeLog.in_memory(partitions=3)
        self.embedder = embedder("model", gateway)
        self.t7 = EmbedStep(self.embedder)
        self.processed: dict[str, Processed] = {}
        self.forgotten: dict[str, int] = {}
        self.decided: list[tuple[DiffResult, Processed | None]] = []
        self._pending: Processed | None = None
        self.first_record = len(gateway.log.records())
        forget = forgetter(gateway, self._drop)

        def forget_and_count(document_key: str) -> None:
            self.forgotten[document_key] = forget(document_key)

        then = chunk_after_t3(chunk_step(), self._keep, classify_step(), t7=self.t7)
        self.t2 = diff_step(
            self.changelog, parse_after_t2(parse_step(self.changelog), then, forget_and_count)
        )
        api = DocsApi(docs)
        people = PersonMap(load_people(IdentityApi(idp)))
        sink, store = self.changelog.publish, self.changelog.store_original
        known = self.changelog.known(SOURCE)
        self.webhook = DocsWebhookConnector(api, sink, store, people, known=known)
        self.listing = DocsFullListingConnector(api, sink, store, people, known=known)

    @property
    def fake(self) -> bool:
        return self.gateway.embedding_model("embed")[0].startswith("fake:")

    def _keep(self, processed: Processed) -> None:
        self._pending = processed
        if processed.parse.parsed is not None and processed.chunks is not None:
            self.processed[processed.parse.event.document_key] = processed

    def _drop(self, document_key: str) -> None:
        self.processed.pop(document_key, None)

    def run(self) -> list[tuple[DiffResult, Processed | None]]:
        """T2 drains its group; T3 to T7 run on whatever T2 sends on."""
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

    def calls(self) -> tuple[int, int, int]:
        """Embedding requests, texts sent and texts from the cache since the story started."""
        records = self.gateway.log.records()[self.first_record :]
        requests = [r for r in records if r.requested]
        sent = sum(r.items for r in requests if r.outcome == "ok")
        cached = sum(r.items for r in records if r.outcome == "hit")
        return len(requests), sent, cached


def _say(echo: Echo, text: str, indent: str = "   ") -> None:
    for line in textwrap.wrap(text, width=WIDTH, initial_indent=indent, subsequent_indent=indent):
        echo(line)


def _preview(vector: tuple[float, ...] | None, shown: int = 4) -> str:
    if vector is None:
        return "none"
    head = ", ".join(f"{v:+.4f}" for v in vector[:shown])
    return f"[{head}, ... {len(vector) - shown} more]"


def _then(action: DiffAction, processed: Processed | None, forgotten: int | None) -> str:
    if processed is not None and processed.vectors is not None:
        vectors = processed.vectors
        return f"T3-T7: {vectors.sent} chunk(s) sent, {vectors.cached} from the cache"
    if action is DiffAction.PURGE:
        n = forgotten or 0
        return f"purged: chunks and {n} cached vector{'' if n == 1 else 's'} forgotten"
    return {
        DiffAction.SKIP: "T3-T7 not run (same text: same vectors)",
        DiffAction.ACL_ONLY: "T3-T7 not run (a vector comes from the text, not its readers)",
    }.get(action, "T3-T7 not run")


# ------------------------------------------------------------------------ scenes


def run_t7_demo(
    docs: httpx.Client, idp: httpx.Client, gateway: ModelGateway, echo: Echo = typer.echo
) -> Story:
    """The whole story against the docs tool. Returns it for inspection."""
    call(docs, "POST", "/reset")
    call(idp, "POST", "/reset")
    story = Story(docs, idp, echo, gateway)

    echo("S10 · T7 Embed: what a vector is and why the model ID is stored\n")
    _say(
        echo,
        "0. T7 turns each chunk into a vector: a fixed-length list of numbers, placed so "
        "that texts with a similar meaning get vectors pointing in a similar direction, "
        "in any language the model knows. Search (S13) compares a question's vector with "
        "the chunks'. Every vector is stored with the ID of what made it.",
        indent="",
    )
    echo(f"   Embedder: {story.embedder.model_id}")
    echo(f"   ({story.embedder.dimensions} numbers per vector, through the gateway's embed task)")
    if story.fake:
        _say(
            echo,
            "FAKE MODEL: these vectors come from a hash of each text. The plumbing is real "
            "(cache, delete, IDs); the similarities below mean nothing.",
        )
    echo("")
    _similar_and_different(story)
    _initial_load(story)
    _one_chunk(story)
    with serving(create_receiver_app(story.webhook.receive)) as receiver:
        hook = call(docs, "POST", "/subscriptions", json={"url": receiver.url + DOCS_WEBHOOK_PATH})
        try:
            _changes(story)
        finally:
            call(docs, "DELETE", f"/subscriptions/{hook['id']}")
    _model_id(story)
    _summary(story)
    return story


def _similar_and_different(story: Story) -> None:
    echo = story.echo
    echo("1. Similar and different sentences, one request through the gateway:")
    for label, language, text in SENTENCES:
        echo(f"   {label}  {language}  {text}")
    vectors = story.embedder.embed([text for _, _, text in SENTENCES]).vectors
    labels = [label for label, _, _ in SENTENCES]
    echo("   How close each pair points (cosine: 1 = same direction, 0 = unrelated):")
    echo("        " + "".join(f"{label:>7}" for label in labels))
    for label, row in zip(labels, vectors, strict=True):
        cells = "".join(f"{cosine(row, other):>7.2f}" for other in vectors)
        echo(f"     {label} {cells}")
    ranked = sorted(
        ((cosine(vectors[0], v), label) for v, label in zip(vectors[1:], labels[1:], strict=True)),
        reverse=True,
    )
    echo("   Closest to A: " + ", ".join(f"{label} {score:.2f}" for score, label in ranked) + ".")
    if story.fake:
        _say(
            echo,
            "With the fake model every pair is close to 0: its numbers come from a hash, so "
            "the Italian and Spanish questions are as far from A as the office hours.",
        )
    else:
        _say(
            echo,
            "A, B and C are the same question in three languages; D and E are about something "
            "else. A multilingual model puts the first three close together, so a question in "
            "Italian can find a policy written in English. Read the order, not the value: "
            "unrelated texts rarely score 0.",
        )
    echo("")


def _initial_load(story: Story) -> None:
    echo = story.echo
    echo("2. Initial load: full listing -> T2 -> T3 -> T4 -> T5 -> T7 (no header, rules).")
    echo(f"   full listing sync() -> {story.listing.sync()} events")
    for _, processed in story.run():
        if processed is None or processed.vectors is None:
            continue
        vectors = processed.vectors
        echo(
            f"   {processed.parse.event.source_id:<10} {len(vectors.chunks)} chunks  "
            f"{vectors.sent} sent, {vectors.cached} from the cache"
        )
    kept = story.kept()
    requests, sent, cached = story.calls()
    embedded = sum(1 for chunk in kept if chunk.vector is not None)
    echo(f"   {embedded} of {len(kept)} chunks from {len(story.processed)} pages have a vector.")
    echo(
        f"   Embedding calls so far: {requests} requests, {sent} texts sent, "
        f"{cached} from the cache.\n"
    )


def _followed(story: Story) -> Chunk:
    chunks = story.page(FOLLOWED).final
    return next((c for c in chunks if c.section and c.section[-1] == FOLLOWED_SECTION), chunks[-1])


def _one_chunk(story: Story) -> None:
    echo = story.echo
    chunk = _followed(story)
    echo(f"3. One chunk and its vector: {chunk.chunk_id}.")
    echo("   What T7 sent (input@1: context header, section trail, text):")
    for line in embed_input(chunk).splitlines():
        for wrapped in textwrap.wrap(line, width=WIDTH - 6) or [""]:
            echo(f"      {wrapped}")
    vector = chunk.vector or ()
    length = sum(v * v for v in vector) ** 0.5
    echo(f"   vector:          {_preview(chunk.vector)}  length {length:.3f}")
    echo(f"   embedding_model: {chunk.embedding_model}")
    echo(
        f"   publishable:     {chunk.publishable}  (sensitivity {chunk.sensitivity}, "
        f"vector yes, ACL {'yes' if chunk.acl else 'no'})"
    )
    _say(
        echo,
        "No single number means anything: the meaning is in the direction of the whole "
        "list. T7 scales float vectors to length 1, so comparing two is one dot product. "
        "The chunk is still not searchable: T8 (S11) has yet to set who may read it.",
    )
    echo("")


def _changes(story: Story) -> None:
    echo, docs = story.echo, story.docs
    echo("4. Only changed text is embedded again; a delete takes its vectors with it.")
    echo(f"   dana changes one sentence of {EDITED} ('{FOLLOWED_SECTION}');")
    echo(f"   lucia removes hr-team from {REVOKED}'s readers; dana deletes {DELETED}:")
    page = call(docs, "GET", f"/pages/{EDITED}")
    body = str(page["body"]).replace("up to **5 unused days**", "up to **7 unused days**")
    call(docs, "PUT", f"/pages/{EDITED}", json={"body": body}, headers=DANA)
    call(docs, "PUT", f"/pages/{REVOKED}/acl", json={"groups": ["es-employees"]}, headers=LUCIA)
    call(docs, "DELETE", f"/pages/{DELETED}", headers=DANA)
    story.webhook.sync()
    for result, processed in story.run():
        event, action = result.decision.event, result.decision.action
        then = _then(action, processed, story.forgotten.get(event.document_key))
        echo(f"   {event.source_id} v{event.version}: T2 {action.value} -> {then}")
    _say(
        echo,
        "Each chunk's vector is cached under a hash of its model and its text (D16 #8), "
        "tagged with its document. An edit sends only the chunk that changed; a rebuild "
        "from the change log would send none. A deleted page's vectors are dropped, "
        "because a vector can give away the text it came from.",
    )
    echo("")


def _model_id(story: Story) -> None:
    echo = story.echo
    chunk = _followed(story)
    current = story.embedder.model_id
    production = model_id_of(PRODUCTION, 1024, int8=True)
    echo("5. Why the model ID is stored with every vector.")
    echo(f"   This chunk's vector:   {chunk.embedding_model}")
    echo(f"   Against today's model: {vector_state(chunk, current).value}")
    echo(f"   Against {production}: {vector_state(chunk, production).value}")
    kept = story.kept()
    stale = sum(1 for c in kept if vector_state(c, production) is VectorState.STALE)
    _say(
        echo,
        "Two models put meaning in different directions: a bge-m3 vector compared with a "
        "Cohere one gives noise, even when both have 1024 numbers. So vectors are compared "
        "only with vectors of the same ID. Switching to Cohere Embed v4 (the production "
        f"design) would make all {stale} chunks stale; the IDs say exactly which ones to "
        "embed again, as a planned batch, and the cache key holds the model too, so no old "
        "vector is reused by mistake. The same goes for a new input recipe (input@2).",
    )
    echo("")


def _summary(story: Story) -> None:
    echo = story.echo
    counts = Counter(result.decision.action for result, _ in story.decided)
    line = ", ".join(f"{a.value} {counts[a]}" for a in DiffAction)
    runs = sum(1 for _, p in story.decided if p is not None and p.vectors is not None)
    kept = story.kept()
    requests, sent, cached = story.calls()
    records = story.gateway.log.records()[story.first_record :]
    spent = sum(r.cost_usd or 0.0 for r in records)
    publishable = sum(1 for chunk in kept if chunk.publishable)
    echo(f"6. T2 decided {len(story.decided)} events: {line}.")
    echo(f"   T7 ran {runs} times: once per 'process', never for the others.")
    echo(
        f"   Embedding calls in this run: {requests} requests, {sent} texts sent, "
        f"{cached} from the cache, ${spent:.2f}."
    )
    echo(f"   Publishable chunks: {publishable} of {len(kept)}: each has a sensitivity and")
    echo("   a vector now, but no ACL until T8 (S11), so none could be searched.")


# -------------------------------------------------------------------------- CLI


def demo_gateway(fake: bool) -> tuple[ModelGateway, str | None]:
    """The gateway for the story, and why it is the offline one when it had to be.

    The configured model gets a fresh in-memory cache, so the story counts its own
    requests; its calls still go to the configured cost log (the inspector's gateway
    panel shows them).
    """
    offline = build_gateway(FAKE_CONFIG, cache=InMemoryCache(), log=InMemoryCostLog())
    if fake:
        return offline, None
    gateway = build_gateway(load_settings().gateway, cache=InMemoryCache())
    down = probe(gateway)
    if down is None:
        return gateway, None
    return offline, f"T7's model cannot answer ({down}); running on a fake embedding model"


@demo_app.command("t7")
def t7(
    fake: Annotated[
        bool,
        typer.Option("--fake", help="Offline: a fake embedding model, numbers with no meaning."),
    ] = False,
    url: Annotated[
        str | None, typer.Option(help="A running mock docs service, e.g. http://127.0.0.1:8081.")
    ] = None,
    idp_url: Annotated[
        str | None, typer.Option(help="A running mock identity provider (port 8083).")
    ] = None,
) -> None:
    """S10 · T7 Embed: what a vector is and why the model ID is stored."""
    try:
        gateway, warning = demo_gateway(fake)
        if warning is not None:
            help_ = embed_help("ekb demo t7", "--fake")
            typer.secho(f"Note: {warning}.\n{help_}\n", fg="yellow")
        with ExitStack() as stack:
            docs = stack.enter_context(mock_client(DOCS, url))
            idp = stack.enter_context(mock_client(IDP, idp_url))
            run_t7_demo(docs, idp, gateway)
    except (MockStartError, httpx.HTTPError, LookupError, GatewayError, ValueError) as exc:
        raise fail(exc) from exc
