"""The worker pipeline (S12): T2 → T8 on the bus, end to end, with options from config.

Box 4 of the architecture as one consumer of the change log. One consumer
group, ``worker``, reads every event; for each one, in the bus's order for its
item, one handler runs the whole write path::

    delivery ─▶ T2 ─┬ process, a page   ─▶ T8 readers first ▶ T3 ▶ T4 ▶ T5 ▶ T6 ▶ T7 ▶ T8 publish
                    ├ process, a record ─▶ T3 "not a document" ▶ entity | permission store
                    ├ acl_only          ─▶ T8 fast lane
                    ├ skip              ─▶ nothing
                    └ purge             ─▶ search, readers, membership, entity, model cache
                    ▼
          registry written last; the bus commits; one Trace per attempt

**Why one handler, not one consumer per step.** T2 writes the registry only
after every later step succeeded (S4), and the bus commits only after that, so
a failure anywhere means "not done, deliver again" and nothing is lost or half
applied. Per-item order holds because the whole chain runs inside one event.
Separate topics per step would need parsed text and chunks stored between
steps, and their own ordering; nothing is stored between steps here, and a
retry starts again from T3 (cached model replies make that cheap, D16 #1).

**Options come from config** (:mod:`ekb.app.choices`); this module only builds
what the choices name and routes T2's decisions.

**Failures.** Each attempt is traced. A failed one raises
:class:`EventFailedError`: the event stays pending and comes back. After
``max_attempts`` the event is parked in the dead letters, its item withdrawn,
and the worker moves on (:mod:`ekb.app.deadletters`). A delete is never parked.

**Re-links (S16).** When the entity store changes (a hire, a leaver, a renamed
team), the worker notes it; :meth:`Pipeline.relink`, run after each drain,
processes again from their stored originals the published pages whose links
are now stale, and, on listing rounds, the pages whose context headers failed
(:mod:`ekb.app.relinks`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

from ekb.app.changelog import ChangeLog
from ekb.app.choices import Choices
from ekb.app.chunk import Processed, chunker, header_writer
from ekb.app.classify import classify_step
from ekb.app.deadletters import DeadLetters, Parked
from ekb.app.embed import embed_step
from ekb.app.entities import RECORD_SOURCES, entity_store, link_step
from ekb.app.gateway import forget_offline_replies
from ekb.app.parse import parse_step
from ekb.app.permissions import (
    membership_sync,
    permission_step,
    permission_store,
    search_store,
    unpublisher,
)
from ekb.app.relinks import Relinks
from ekb.changelog.bus.consume import drain
from ekb.changelog.bus.port import Delivery, EventBus
from ekb.changelog.rawstore.port import RawStore
from ekb.changelog.registry.port import DocumentRegistry
from ekb.contracts import (
    ChangeEvent,
    ChangeOp,
    DiffAction,
    StepOutcome,
    StepTrace,
    Trace,
    TraceStatus,
)
from ekb.platform import registry
from ekb.platform.gateway.config import OptionChoice
from ekb.platform.gateway.gateway import ModelGateway
from ekb.process.t2_diff.port import Differ
from ekb.process.t2_diff.step import DiffResult, DiffStep
from ekb.process.t3_parse.step import ParseStep
from ekb.process.t4_chunk.header import header_failed
from ekb.process.t4_chunk.step import ChunkStep
from ekb.process.t4_chunk.step import summary as chunk_summary
from ekb.process.t5_classify.step import ClassifyStep
from ekb.process.t5_classify.step import summary as classify_summary
from ekb.process.t6_entities.relink import EntityChange, candidates, stale
from ekb.process.t6_entities.step import LinkStep
from ekb.process.t6_entities.step import summary as entities_summary
from ekb.process.t6_entities.sync import EntitySync, RecordResult
from ekb.process.t7_embed.step import EmbedStep
from ekb.process.t8_permissions.step import PermissionResult, PermissionStep
from ekb.process.t8_permissions.sync import MembershipSync
from ekb.stores.entities.port import EntityStore
from ekb.stores.permissions.port import PermissionStore
from ekb.stores.search.port import SearchStore

WORKER_GROUP = "worker"
"""The worker's consumer group. It replaced the inspector's ``t2_diff`` group."""
MAX_ATTEMPTS = 3
"""Failed attempts at one event before it is parked in the dead letters."""


# ------------------------------------------------------------------ building


def _built(choices: Choices, step: str) -> object:
    return registry.create(step, choices.option(step), **choices.params(step))


def _refuse(step: str, choices: Choices, port: str) -> TypeError:
    return TypeError(f"{step} option {choices.option(step)!r} does not implement {port}")


def build_changelog(choices: Choices) -> ChangeLog:
    """Box 3 as the choices name it: the bus, the document registry and the raw store."""
    bus, records, originals = (_built(choices, s) for s in ("bus", "registry", "rawstore"))
    if not isinstance(bus, EventBus):
        raise _refuse("bus", choices, "EventBus")
    if not isinstance(records, DocumentRegistry):
        raise _refuse("registry", choices, "DocumentRegistry")
    if not isinstance(originals, RawStore):
        raise _refuse("rawstore", choices, "RawStore")
    return ChangeLog(bus, records, originals)


def differ(choices: Choices) -> Differ:
    """T2's option as the choices name it (``content_hash``)."""
    built = _built(choices, "t2_diff")
    if not isinstance(built, Differ):
        raise _refuse("t2_diff", choices, "Differ")
    return built


@dataclass(frozen=True)
class Stores:
    """Box 5's stores the write path fills."""

    search: SearchStore
    permissions: PermissionStore
    entities: EntityStore


def build_stores(choices: Choices, changelog: ChangeLog) -> Stores:
    """The search, permission and entity stores the choices name. The first two hide a
    deleted item's data as soon as its delete is accepted (the change log's lifetimes)."""
    return Stores(
        search=search_store(choices.option("search"), current_lifetime=changelog.current_lifetime),
        permissions=permission_store(
            choices.option("permissions"), current_lifetime=changelog.current_lifetime
        ),
        entities=entity_store(choices.option("entities")),
    )


@dataclass(frozen=True)
class Steps:
    """Every step and sync the worker runs, built once from the choices."""

    differ: Differ
    t3: ParseStep
    t4: ChunkStep
    t5: ClassifyStep
    t6: LinkStep
    t7: EmbedStep
    t8: PermissionStep
    entities: EntitySync
    """HR records → the entity store (S9)."""
    memberships: MembershipSync
    """Identity users → the permission store's memberships (S11)."""


def build_steps(
    choices: Choices, changelog: ChangeLog, stores: Stores, gateway: ModelGateway | None = None
) -> Steps:
    """T2 to T8 as the choices name them. A model option needs ``gateway``."""
    header = header_writer(choices.option("t4_header"), gateway)
    return Steps(
        differ=differ(choices),
        t3=parse_step(changelog, choices.option("t3_parse")),
        t4=ChunkStep(chunker(choices.option("t4_chunk"), **choices.params("t4_chunk")), header),
        t5=classify_step(choices.option("t5_classify"), gateway),
        t6=link_step(stores.entities, choices.option("t6_entities")),
        t7=embed_step(choices.option("t7_embed"), gateway, **choices.params("t7_embed")),
        t8=permission_step(stores.permissions, stores.search, choices.option("t8_permissions")),
        entities=EntitySync(stores.entities, changelog.rawstore, RECORD_SOURCES),
        memberships=membership_sync(changelog, stores.permissions),
    )


# ------------------------------------------------------------------ outcomes


@dataclass(frozen=True)
class Outcome:
    """What the worker did with one event (or one rebuild)."""

    delivery: Delivery | None
    """``None`` for a rebuild from the stored original."""
    trace: Trace
    diff: DiffResult | None = None
    """T2's result; ``None`` when T2 did not decide (parked, or a rebuild)."""
    readers: PermissionResult | None = None
    """T8 before the content: the fast lane, or readers first on an edit."""
    processed: Processed | None = None
    """T3 to T8 (or the record stores) for a ``process`` decision."""
    removed: int = 0
    """Chunks a purge took out of the search store."""


Observer = Callable[[Outcome], object]
"""Sees each outcome before T2 records it: raising means "not done, deliver again"."""
TraceListener = Callable[[Trace], object]
"""Sees every attempt's final trace, failed ones included."""


class EventFailedError(RuntimeError):
    """An attempt failed; the event stays pending and is delivered again."""

    def __init__(self, trace: Trace, max_attempts: int) -> None:
        self.trace = trace
        where = f" at {trace.failed_step}" if trace.failed_step else ""
        super().__init__(
            f"{trace.document_key} v{trace.version}: attempt {trace.attempt} of "
            f"{max_attempts} failed{where}: {trace.error}"
        )


class _Run:
    """Collects one attempt's trace: each step, its outcome and its time."""

    def __init__(
        self, event: ChangeEvent, delivery: Delivery | None, attempt: int, *, rebuild: bool = False
    ) -> None:
        self.event = event
        self.delivery = delivery
        self.attempt = attempt
        self.rebuild = rebuild
        self.started_at = datetime.now(UTC)
        self.steps: list[StepTrace] = []
        self.outcome: Outcome | None = None
        self._start = perf_counter()

    def step[R](self, name: str, work: Callable[[], R], detail: Callable[[R], str]) -> R:
        """Run one step and trace it; a step that raises is traced as failed, then re-raised."""
        start = perf_counter()
        try:
            result = work()
        except Exception as exc:
            self.steps.append(
                StepTrace(step=name, outcome=StepOutcome.FAILED, detail=_error(exc), ms=_ms(start))
            )
            raise
        self.steps.append(
            StepTrace(step=name, outcome=StepOutcome.DONE, detail=detail(result), ms=_ms(start))
        )
        return result

    def note(self, name: str, detail: str, *, since_start: bool = False) -> None:
        """A step with nothing to time (or timed from the start of the attempt)."""
        ms = _ms(self._start) if since_start else 0.0
        self.steps.append(StepTrace(step=name, outcome=StepOutcome.DONE, detail=detail, ms=ms))

    def failed_at(self, name: str, exc: BaseException) -> None:
        """Trace a failure that happened outside any traced step."""
        if not any(s.outcome is StepOutcome.FAILED for s in self.steps):
            self.steps.append(
                StepTrace(step=name, outcome=StepOutcome.FAILED, detail=_error(exc), ms=0.0)
            )

    def trace(self, status: TraceStatus, action: DiffAction | None, error: str = "") -> Trace:
        event, delivery = self.event, self.delivery
        return Trace(
            document_key=event.document_key,
            source=event.source,
            op=event.op,
            version=event.version,
            partition=delivery.partition if delivery is not None else None,
            offset=delivery.offset if delivery is not None else None,
            attempt=self.attempt,
            action=action,
            status=status,
            steps=tuple(self.steps),
            error=error,
            started_at=self.started_at,
            ms=_ms(self._start),
        )


def _ms(start: float) -> float:
    return round((perf_counter() - start) * 1000, 3)


def _error(exc: BaseException) -> str:
    return type(exc).__name__


def _record_detail(result: RecordResult) -> str:
    """The entity store's change by ID only: a trace holds no names (they outlive a delete)."""
    if result.entity is None:
        return "defines no entity (no identity user for this employee: fail closed)"
    verb = "added" if result.replaced is None else "updated"
    return f"{result.entity.entity_id} {verb}"


def _first_readers(result: PermissionResult | None) -> str:
    if result is None:
        return "a new document: its readers go on with T8, after T7"
    return result.reason


# ------------------------------------------------------------------ the worker


class Pipeline:
    """The worker: one consumer group over the change log, T2 → T8 for every event."""

    def __init__(
        self,
        steps: Steps,
        changelog: ChangeLog,
        stores: Stores,
        *,
        gateway: ModelGateway | None = None,
        model_cache: OptionChoice | None = None,
        observers: Sequence[Observer] = (),
        on_trace: TraceListener | None = None,
        group: str = WORKER_GROUP,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.steps = steps
        self.changelog = changelog
        self.stores = stores
        self.gateway = gateway
        self._model_cache = model_cache
        self.group = group
        self.max_attempts = max_attempts
        self.dead_letters = DeadLetters()
        self.relinks = Relinks()
        self.t2 = DiffStep(steps.differ, changelog.registry, changelog.rawstore, self._after_t2)
        self._observers = tuple(observers)
        self._on_trace = on_trace
        self._unpublish = unpublisher(stores.search, stores.permissions, steps.memberships)
        self._attempts: dict[tuple[int, int], int] = {}
        self._run: _Run | None = None
        self.purge_guard: AbstractContextManager[object] = nullcontext()

    # ---- consuming

    def run(self) -> int:
        """Drain the worker's group once: every pending event, in order; return how many.

        Raises :class:`EventFailedError` at the first event that fails without being
        parked: it and everything after it stay pending, to be delivered again.
        """
        return drain(self.changelog.bus, self.group, self.handle)

    def handle(self, delivery: Delivery) -> Outcome:
        """One event, end to end. Returning means "done": the bus may commit it."""
        if delivery.event.op is ChangeOp.DELETE:
            with self.purge_guard:
                return self._handle(delivery, replay=False)
        return self._handle(delivery, replay=False)

    def _set_visible(self, key: str, visible: bool) -> None:
        self.stores.search.set_visible(key, visible=visible)
        self.stores.permissions.set_visible(key, visible=visible)

    def _handle(self, delivery: Delivery, *, replay: bool) -> Outcome:
        event = delivery.event
        key = event.document_key
        discarded = 0
        if self.dead_letters.holds(key):
            if event.op is not ChangeOp.DELETE:
                return self._park_behind(delivery)
            discarded = len(self.dead_letters.take(key))  # a delete supersedes them
        where = (delivery.partition, delivery.offset)
        run = _Run(event, delivery, self._attempts.get(where, 0) + 1)
        if discarded:
            run.note(
                "dead letters",
                f"{discarded} parked events discarded: the delete supersedes them",
            )
        self._run = run
        try:
            self.t2.handle(event)
        except Exception as exc:
            decided = any(step.step == "T2 diff" for step in run.steps)
            run.failed_at("finish" if decided else "T2 diff", exc)
            return self._failed(run, exc, replay=replay)
        finally:
            self._run = None
        self._attempts.pop(where, None)
        if not replay and self.changelog.latest_delivery(delivery):
            self._set_visible(key, True)
        return self._finish(run)

    # ---- after T2

    def _after_t2(self, result: DiffResult) -> None:
        """T2's ``downstream``: route its decision, then let the observers see it.

        Runs before T2 writes the registry: if anything here raises, nothing is
        recorded and the event comes back (S4)."""
        run = self._run
        if run is None:  # pragma: no cover - DiffStep is only driven by handle()
            raise RuntimeError("T2's downstream ran outside an attempt")
        decision = result.decision
        purged = f"; {result.purged} originals purged" if result.purged else ""
        run.note("T2 diff", f"{decision.action.value}: {decision.reason}{purged}", since_start=True)
        event, key = decision.event, decision.event.document_key
        readers = processed = None
        removed = 0
        match decision.action:
            case DiffAction.PROCESS:
                status, readers, processed = self._process(run, event)
                self.dead_letters.restored(key)
            case DiffAction.ACL_ONLY:
                status, readers = self._fast_lane(run, event)
            case DiffAction.SKIP:
                status = TraceStatus.SKIPPED
            case DiffAction.PURGE:
                status, removed = TraceStatus.PURGED, self._purge(run, key)
                self.dead_letters.restored(key)
        outcome = Outcome(
            delivery=run.delivery,
            trace=run.trace(status, decision.action),
            diff=result,
            readers=readers,
            processed=processed,
            removed=removed,
        )
        run.outcome = outcome
        for observer in self._observers:
            observer(outcome)

    def _process(
        self, run: _Run, event: ChangeEvent
    ) -> tuple[TraceStatus, PermissionResult | None, Processed]:
        """New or changed content: readers first, then T3 … T8 (or a record's store)."""
        steps = self.steps
        readers = None
        if steps.t3.handles(event.source):
            # Before the content: a reader removed in the same save loses access now,
            # even if a model is down and the new text has to wait (S11).
            readers = run.step(
                "T8 readers first", lambda: steps.t8.readers_first(event), _first_readers
            )
        parse = run.step("T3 parse", lambda: steps.t3.handle(event), lambda r: r.reason)
        if parse.parsed is None:
            record = membership = None
            if steps.entities.handles(event.source):
                record = run.step(
                    "entity store", lambda: steps.entities.handle(event), _record_detail
                )
                self.relinks.changed(EntityChange(record.replaced, record.entity))
            if steps.memberships.handles(event.source):
                membership = run.step(
                    "permission store", lambda: steps.memberships.handle(event), lambda r: r.reason
                )
            if record is None and membership is None:
                run.note(
                    "no store yet",
                    "rows stay tables, queried with SQL: the metrics "
                    "store comes in S14b; never chunked or embedded (D7)",
                )
            processed = Processed(parse, None, record=record, membership=membership)
            return TraceStatus.RECORD, readers, processed
        document = parse.parsed
        chunks = run.step(
            "T4 chunk",
            lambda: steps.t4.handle(document),
            lambda r: f"chunked with {r.chunker_id}: {chunk_summary(r.chunks, r.max_tokens)}",
        )
        labels = run.step(
            "T5 classify",
            lambda: steps.t5.handle(document, chunks.chunks),
            lambda r: f"classified with {r.classifier_id}: {classify_summary(r.chunks)}",
        )
        links = run.step(
            "T6 entities",
            lambda: steps.t6.handle(document, labels.chunks),
            lambda r: f"linked with {r.linker_id}: {entities_summary(r.chunks)}",
        )
        vectors = run.step(
            "T7 embed", lambda: steps.t7.handle(document, links.chunks), lambda r: r.reason
        )
        published = run.step(
            "T8 publish",
            lambda: steps.t8.publish(event, document, vectors.chunks),
            lambda r: r.reason,
        )
        processed = Processed(parse, chunks, labels, links, vectors=vectors, published=published)
        return TraceStatus.PUBLISHED, readers, processed

    def _fast_lane(
        self, run: _Run, event: ChangeEvent
    ) -> tuple[TraceStatus, PermissionResult | None]:
        """Only the readers changed: T8 puts them on the published chunks; nothing else runs."""
        if not self.steps.t3.handles(event.source):
            run.note("T8 fast lane", "a record: nothing of it is published, no readers to change")
            return TraceStatus.RECORD, None
        applied = run.step(
            "T8 fast lane", lambda: self.steps.t8.apply_readers(event), lambda r: r.reason
        )
        return TraceStatus.READERS, applied

    def _purge(self, run: _Run, key: str) -> int:
        """Deleted in the source: out of every store. Search first (the deletion gate),
        the model cache last: a retry after a failure repeats every drop harmlessly."""
        removed = run.step(
            "purge search",
            lambda: self._unpublish(key),
            lambda n: (
                f"{n} chunks out of the search store; readers record and membership "
                "(if any) dropped"
            ),
        )
        gone = run.step(
            "purge entity",
            lambda: self.steps.entities.forget(key),
            lambda e: f"{e.entity_id} removed" if e else "no entity",
        )
        if gone is not None:
            self.relinks.changed(EntityChange(gone, None))
        if self.gateway is not None:
            run.step(
                "purge model cache",
                lambda: self._forget_replies(key),
                lambda n: f"{n} cached model replies and vectors forgotten",
            )
        elif self._model_cache is not None:
            forget_offline_replies(self._model_cache, key)
        return removed

    def _forget_replies(self, key: str) -> int:
        return self.gateway.forget(key) if self.gateway is not None else 0

    # ---- failures and the dead letters

    def _failed(self, run: _Run, exc: Exception, *, replay: bool) -> Outcome:
        delivery = run.delivery
        if delivery is None:  # pragma: no cover - rebuilds have their own handling
            raise exc
        where = (delivery.partition, delivery.offset)
        self._attempts[where] = run.attempt
        error = _error(exc)
        run.outcome = None  # whatever ran was not kept: T2 recorded nothing
        key = run.event.document_key
        if self.steps.memberships.handles(run.event.source) or any(
            step.outcome is StepOutcome.FAILED
            and step.step in {"T2 diff", "T8 readers first", "T8 fast lane", "T8 publish"}
            for step in run.steps
        ):
            self._set_visible(key, False)
        if replay or run.event.op is ChangeOp.DELETE or run.attempt < self.max_attempts:
            trace = run.trace(TraceStatus.FAILED, None, error)
            self._emit(trace)
            raise EventFailedError(trace, self.max_attempts) from exc
        # Fail closed before moving on: if withdrawing fails too, this raises and the
        # event stays pending.
        self._set_visible(key, False)
        start = perf_counter()
        try:
            withdrawn = self._unpublish(key)
        except Exception as withdrawal_error:
            detail = _error(withdrawal_error)
            run.steps.append(
                StepTrace(
                    step="dead letters", outcome=StepOutcome.FAILED, detail=detail, ms=_ms(start)
                )
            )
            trace = run.trace(TraceStatus.FAILED, None, error)
            self._emit(trace)
            raise EventFailedError(trace, self.max_attempts) from withdrawal_error
        self.dead_letters.withdraw(run.event.document_key)
        self.dead_letters.park(delivery, error)
        self._attempts.pop(where, None)
        run.note(
            "dead letters",
            f"parked after {run.attempt} failed attempts; withdrawn until it is retried "
            f"({withdrawn} chunks out of the search store)",
        )
        return self._finish(run, TraceStatus.PARKED, error)

    def _park_behind(self, delivery: Delivery) -> Outcome:
        """An event of an item with parked events waits behind them, in order."""
        event = delivery.event
        first = self.dead_letters.first(event.document_key)
        run = _Run(event, delivery, 1)
        waiting = self.dead_letters.park(delivery)
        behind = f"v{first.delivery.event.version}" if first is not None else "an earlier event"
        run.note(
            "dead letters",
            f"parked behind {behind} ({waiting} parked for this item); replayed with it on retry",
        )
        return self._finish(run, TraceStatus.PARKED)

    def retry_parked(self, document_key: str | None = None) -> list[Outcome]:
        """Replay parked events (of one item, or all), oldest first, one attempt each.

        If one fails again, it and the item's later events go back, in order, and
        the item stays withdrawn. If the whole replay succeeds but republished
        nothing (only readers changes were parked), the item is rebuilt from its
        last stored original.
        """
        keys = [document_key] if document_key else list(self.dead_letters.items())
        outcomes: list[Outcome] = []
        for key in keys:
            parked = self.dead_letters.take(key)
            if not parked:
                continue
            for done, entry in enumerate(parked):
                try:
                    outcomes.append(self._handle(entry.delivery, replay=True))
                except EventFailedError as exc:
                    delivery = entry.delivery
                    self._attempts.pop((delivery.partition, delivery.offset), None)
                    rest = [Parked(delivery, exc.trace.error, entry.parked_at)]
                    self.dead_letters.put_back(key, rest + parked[done + 1 :])
                    break
            else:
                if self.dead_letters.withdrawn(key):
                    last = parked[-1]
                    try:
                        rebuilt = self._rebuild(key, last.delivery.event.original_lifetime)
                    except EventFailedError as exc:
                        self.dead_letters.put_back(
                            key, [Parked(last.delivery, exc.trace.error, last.parked_at)]
                        )
                        continue
                    if rebuilt is not None:
                        outcomes.append(rebuilt)
                if self.changelog.latest_delivery(parked[-1].delivery):
                    self._set_visible(key, True)
        return outcomes

    def _rebuild(self, key: str, lifetime: int) -> Outcome | None:
        """Process an item again from its last stored original (the registry's record)."""
        record = self.changelog.registry.get(key)
        if (
            record is None
            or record.deleted
            or record.raw_pointer is None
            or lifetime != self.changelog.current_lifetime(key)
        ):
            self.dead_letters.restored(key)  # nothing stored to rebuild from
            return None
        why = f"from the stored original of v{record.version} (the registry's record)"
        outcome = self._reprocess(key, lifetime, "rebuild", why, TraceStatus.REBUILT)
        self.dead_letters.restored(key)
        return outcome

    def _reprocess(
        self, key: str, lifetime: int, step: str, why: str, status_if_published: TraceStatus
    ) -> Outcome | None:
        """Run T3 … T8 again on an item's last stored original, with no event to deliver
        it. ``None`` if there is nothing stored for its current lifetime."""
        record = self.changelog.registry.get(key)
        if (
            record is None
            or record.deleted
            or record.raw_pointer is None
            or lifetime != self.changelog.current_lifetime(key)
        ):
            return None
        event = ChangeEvent(
            source=record.source,
            source_id=record.source_id,
            op=ChangeOp.UPSERT,
            version=record.version,
            occurred_at=record.updated_at,
            acl=record.acl,
            raw_pointer=record.raw_pointer,
            content_hash=record.content_hash,
            person_id=record.person_id,
            changed_by=record.changed_by,
            original_lifetime=lifetime,
        )
        run = _Run(event, None, 1, rebuild=True)
        run.note(step, why)
        try:
            status, readers, processed = self._process(run, event)
            status = status_if_published if status is TraceStatus.PUBLISHED else status
            outcome = Outcome(None, run.trace(status, None), None, readers, processed)
            for observer in self._observers:
                observer(outcome)
        except Exception as exc:
            run.failed_at("finish", exc)
            trace = run.trace(TraceStatus.FAILED, None, _error(exc))
            self._emit(trace)
            raise EventFailedError(trace, self.max_attempts) from exc
        return self._finish(run, status, outcome=outcome)

    # ---- re-links (S16)

    def relink(self, *, retry_headers: bool = False) -> list[Outcome]:
        """Process again the pages whose world changed: after each drain.

        1. The entity changes noted since the last call become the published
           pages they affect (:func:`~ekb.process.t6_entities.relink.candidates`).
        2. With ``retry_headers`` (a listing round), pages whose context headers
           failed are queued too, if the header option writes headers at all.
        3. Each queued page whose links are stale (or whose headers failed) is
           processed again from its stored original; one that fails stays queued
           for the next call, still searchable as it was. A page in the dead
           letters is left to its retry, and a page no longer published is dropped.
        """
        changes = self.relinks.take_changes()
        if changes:
            affected = candidates(self.stores.search.chunks(), changes)
            for key, reasons in affected.items():
                for reason in reasons:
                    self.relinks.queue(key, reason)
        if retry_headers and not self.steps.t4.header_id.startswith("none"):
            for key in self.stores.search.documents():
                if any(header_failed(c.header_id) for c in self.stores.search.chunks(key)):
                    self.relinks.queue(key, "its context headers failed", headers=True)
        outcomes: list[Outcome] = []
        for pending in self.relinks.pages():
            key = pending.document_key
            if self.dead_letters.holds(key) or self.dead_letters.withdrawn(key):
                self.relinks.done(key)  # its retry processes it against today's entities
                continue
            chunks = self.stores.search.chunks(key)
            if not chunks:
                self.relinks.done(key)  # deleted, withdrawn or never published
                continue
            links_stale = stale(chunks, self.stores.entities.entities())
            if not links_stale and not pending.headers:
                self.relinks.done(key)  # its links are already today's
                continue
            step, status = (
                ("re-link", TraceStatus.RELINKED)
                if links_stale
                else ("rebuild", TraceStatus.REBUILT)
            )
            try:
                outcome = self._reprocess(
                    key, chunks[0].original_lifetime, step, "; ".join(pending.reasons), status
                )
            except EventFailedError:
                continue  # traced as failed; queued for the next round
            self.relinks.done(key)
            if outcome is not None:
                outcomes.append(outcome)
        return outcomes

    # ---- traces

    def _finish(
        self,
        run: _Run,
        status: TraceStatus | None = None,
        error: str = "",
        *,
        outcome: Outcome | None = None,
    ) -> Outcome:
        """The final trace (total time included) on the outcome, and to the listener."""
        done = outcome or run.outcome
        if status is None:
            if done is None:  # pragma: no cover - a successful attempt always routed
                raise RuntimeError("an attempt finished without an outcome")
            status = done.trace.status
        action = done.trace.action if done is not None else None
        trace = run.trace(status, action, error)
        self._emit(trace)
        if done is None:
            return Outcome(run.delivery, trace)
        return Outcome(done.delivery, trace, done.diff, done.readers, done.processed, done.removed)

    def _emit(self, trace: Trace) -> None:
        if self._on_trace is not None:
            self._on_trace(trace)


def build_pipeline(
    choices: Choices,
    changelog: ChangeLog,
    *,
    stores: Stores | None = None,
    gateway: ModelGateway | None = None,
    observers: Sequence[Observer] = (),
    on_trace: TraceListener | None = None,
    group: str = WORKER_GROUP,
    max_attempts: int = MAX_ATTEMPTS,
) -> Pipeline:
    """The worker for these choices over ``changelog``, filling ``stores`` (built from the
    choices if not given). Options that call a model need ``gateway``."""
    stores = stores or build_stores(choices, changelog)
    return Pipeline(
        build_steps(choices, changelog, stores, gateway),
        changelog,
        stores,
        gateway=gateway,
        model_cache=choices.model_cache,
        observers=observers,
        on_trace=on_trace,
        group=group,
        max_attempts=max_attempts,
    )
