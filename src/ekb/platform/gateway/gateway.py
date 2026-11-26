"""The model gateway: route, reuse, guard, call, log.

``complete(task, prompt, ...)`` goes through four steps:

1. **Route.** The task's candidates come from the configuration, in order. An
   unknown task is an error: there is no default model to fall back on silently.
2. **Reuse.** The cache is asked under every candidate's key, in order. A reply
   any candidate already wrote for exactly this call is returned, no request made.
3. **Guard, then call.** Candidates are tried in order. One is skipped, without a
   request, when it cannot be used (no API key), when its paid price would go past
   the spend cap, when its daily quota is used up, when its provider said "rate
   limited" less than ``cooldown_s`` ago, or when its per-minute limit would mean
   waiting longer than ``max_wait_s`` (a shorter wait is waited out: staying on
   the same model is better than switching). A rate-limited or unavailable model
   moves the call to the next candidate; an invalid request stops it.
4. **Log.** Every hit and every attempt is one record in the cost log: task,
   model, outcome, tokens, cost, latency, the cache key, never the text.

Calls hold both store guards through logging and caching. File-backed guards
coordinate workers across processes, including document eviction during a call.

``embed(task, texts, ...)`` (S10) does the same for vectors, with two
differences. **Reuse is per text**: each text has its own cache key, so only the
texts not seen before are sent, all in one request. And **there is no
failover**: an embedding task names exactly one model (see ``config``), so when
that model is skipped or fails the call fails, and the caller retries later.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from ekb.platform.gateway.config import GatewayConfig
from ekb.platform.gateway.errors import (
    InvalidModelRequestError,
    ModelCallError,
    NoModelAvailableError,
    TaskKindError,
    UnknownTaskError,
)
from ekb.platform.gateway.keys import batch_key, cache_key, embed_key
from ekb.platform.gateway.models import (
    CachedReply,
    CallRecord,
    ChatRequest,
    EmbeddingReply,
    EmbedReply,
    GatewayReply,
    ModelReply,
    Outcome,
    Step,
)
from ekb.platform.gateway.port import CostLog, EmbeddingClient, ModelClient, ResponseCache
from ekb.platform.gateway.usage import (
    MINUTE,
    QuotaUsage,
    all_quota_usage,
    day_start,
    next_reset,
    requests_since,
    spent_usd,
)

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


class ModelGateway:
    def __init__(
        self,
        config: GatewayConfig,
        clients: Mapping[str, ModelClient],
        cache: ResponseCache,
        log: CostLog,
        *,
        embedding_clients: Mapping[str, EmbeddingClient] | None = None,
        max_spend_usd: float | None = None,
        clock: Clock = utc_now,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.cache = cache
        self.log = log
        self.max_spend_usd = config.max_spend_usd if max_spend_usd is None else max_spend_usd
        self._clients = dict(clients)
        self._embedding_clients = dict(embedding_clients or {})
        self._clock = clock
        self._sleep = sleep

    # ------------------------------------------------------------------ queries

    @property
    def tasks(self) -> list[str]:
        return sorted(self.config.tasks)

    def candidates(self, task: str) -> tuple[str, ...]:
        try:
            return self.config.tasks[task]
        except KeyError:
            known = ", ".join(self.tasks) or "none"
            raise UnknownTaskError(f"no route for task {task!r} (tasks: {known})") from None

    def client(self, candidate: str) -> ModelClient | None:
        return self._clients.get(candidate)

    def embedding_client(self, candidate: str) -> EmbeddingClient | None:
        return self._embedding_clients.get(candidate)

    def embedding_model(self, task: str) -> tuple[str, int]:
        """The one ``provider:model`` an embedding task routes to, and its vector size."""
        chain = self.candidates(task)
        if self.config.task_kind(task) != "embedding":
            raise TaskKindError(f"task {task!r} writes text: call complete(), not embed()")
        candidate = chain[0]
        dimensions = self.config.models[candidate].dimensions
        if dimensions is None:  # the configuration refuses this; kept for the type checker
            raise TaskKindError(f"{candidate} declares no dimensions")
        return candidate, dimensions

    def embedding_is_int8(self, task: str) -> bool:
        candidate, _ = self.embedding_model(task)
        return self.config.models[candidate].int8

    def usage(self) -> list[QuotaUsage]:
        return all_quota_usage(self.config, self.log.records(), self._clock())

    def spent_usd(self) -> float | None:
        return spent_usd(self.log.records())

    def forget(self, document_key: str) -> int:
        """A document was deleted: drop the cached replies only it was using, and every
        reply whose prompt quoted it (an answer written from its passages, S16)."""
        with self.cache.guard:
            return self.cache.forget(document_key)

    # --------------------------------------------------------------------- call

    def complete(
        self,
        task: str,
        prompt: str,
        *,
        prompt_version: str,
        max_output_tokens: int,
        system: str = "",
        temperature: float = 0.0,
        json_schema: dict[str, Any] | None = None,
        document_key: str | None = None,
        quotes: Sequence[str] = (),
    ) -> GatewayReply:
        """Answer ``prompt`` for ``task``, from the cache or from the first usable model.

        ``document_key`` tags the cached reply with the document it was about, so
        :meth:`forget` can drop it when that document is deleted. ``quotes`` names
        the documents whose text is in the prompt (an answer's passages): the reply
        is dropped as soon as any one of them is deleted.
        """
        if not prompt_version:
            raise ValueError("prompt_version is required: it is part of the cache key")
        request = ChatRequest(
            prompt=prompt,
            system=system,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            json_schema=json_schema,
        )
        # Always acquire the cache first, including when another process shares only the log.
        with self.cache.guard, self.log.guard:
            chain = self.candidates(task)
            if self.config.task_kind(task) != "chat":
                raise TaskKindError(f"task {task!r} returns vectors: call embed(), not complete()")
            keys = {
                c: cache_key(c, self.config.models[c].params, prompt_version, request)
                for c in chain
            }
            hit = self._from_cache(task, chain, keys, prompt_version, document_key, quotes)
            if hit is not None:
                return hit
            return self._call(task, chain, keys, prompt_version, request, document_key, quotes)

    def _from_cache(
        self,
        task: str,
        chain: Sequence[str],
        keys: Mapping[str, str],
        prompt_version: str,
        document_key: str | None,
        quotes: Sequence[str] = (),
    ) -> GatewayReply | None:
        for candidate in chain:
            entry = self.cache.get(keys[candidate])
            if entry is None:
                continue
            if document_key is not None:
                self.cache.tag(keys[candidate], document_key)
            if quotes:  # a reply cached before its quotes were recorded gets them now
                self.cache.quote(keys[candidate], quotes)
            reply = entry.reply
            self.log.append(
                self._record(
                    task, candidate, prompt_version, keys[candidate], "hit", document_key
                ).model_copy(
                    update={
                        "input_tokens": reply.input_tokens,
                        "output_tokens": reply.output_tokens,
                    }
                )
            )
            return GatewayReply(
                text=reply.text,
                task=task,
                candidate=entry.candidate,
                served_model=reply.served_model,
                cached=True,
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                cost_usd=0.0,
                latency_ms=0,
                key=keys[candidate],
                steps=(Step(candidate=candidate, result="hit"),),
            )
        return None

    def _call(
        self,
        task: str,
        chain: Sequence[str],
        keys: Mapping[str, str],
        prompt_version: str,
        request: ChatRequest,
        document_key: str | None,
        quotes: Sequence[str] = (),
    ) -> GatewayReply:
        steps: list[Step] = []
        records = self.log.records()
        for candidate in chain:
            client = self._clients.get(candidate)
            reason = self._blocked(candidate, client, records)
            if reason is not None or client is None:
                steps.append(Step(candidate=candidate, result=f"skipped: {reason or 'no client'}"))
                continue
            key = keys[candidate]
            started = time.monotonic()
            try:
                reply = client.invoke(request)
            except ModelCallError as exc:
                price = self.config.models[candidate].price
                inputs, outputs = exc.usage or (0, 0)
                unknown = exc.received_reply and exc.usage is None and not price.free
                record = self._record(
                    task, candidate, prompt_version, key, exc.kind, document_key
                ).model_copy(
                    update={
                        "latency_ms": _ms_since(started),
                        "error": exc.detail,
                        "input_tokens": inputs,
                        "output_tokens": outputs,
                        "cost_usd": None if unknown else price.cost(inputs, outputs),
                    }
                )
                self.log.append(record)
                records.append(record)
                steps.append(Step(candidate=candidate, result=f"{exc.kind}: {exc.detail}"))
                if exc.kind == "invalid":
                    raise InvalidModelRequestError(task, candidate, exc.detail) from exc
                continue
            return self._answered(
                task, candidate, prompt_version, key, reply, document_key, started, steps, quotes
            )
        raise NoModelAvailableError(task, steps, self._first_reset(chain))

    def _answered(
        self,
        task: str,
        candidate: str,
        prompt_version: str,
        key: str,
        reply: ModelReply,
        document_key: str | None,
        started: float,
        steps: list[Step],
        quotes: Sequence[str] = (),
    ) -> GatewayReply:
        latency = _ms_since(started)
        price = self.config.models[candidate].price
        cost = (
            price.cost(reply.input_tokens, reply.output_tokens)
            if reply.usage_reported or price.free
            else None
        )
        self.log.append(
            self._record(task, candidate, prompt_version, key, "ok", document_key).model_copy(
                update={
                    "input_tokens": reply.input_tokens,
                    "output_tokens": reply.output_tokens,
                    "cost_usd": cost,
                    "latency_ms": latency,
                }
            )
        )
        self.cache.put(
            key,
            CachedReply(candidate=candidate, reply=reply, created_at=self._clock()),
            document_key,
            quotes,
        )
        steps.append(Step(candidate=candidate, result="ok"))
        return GatewayReply(
            text=reply.text,
            task=task,
            candidate=candidate,
            served_model=reply.served_model,
            cached=False,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            cost_usd=cost,
            latency_ms=latency,
            key=key,
            steps=tuple(steps),
        )

    # -------------------------------------------------------------------- embed

    def embed(
        self,
        task: str,
        texts: Sequence[str],
        *,
        input_version: str,
        document_key: str | None = None,
    ) -> EmbedReply:
        """One vector per text, from the cache where the text was embedded before.

        ``input_version`` says how the caller built the texts (T7's ``input@1``);
        like a prompt version, it is part of every key and the only label the cost
        log keeps. ``document_key`` tags every vector with the document it came
        from, so :meth:`forget` drops a deleted document's vectors too.
        """
        if not input_version:
            raise ValueError("input_version is required: it is part of the cache key")
        asked = list(texts)
        with self.cache.guard, self.log.guard:
            candidate, dimensions = self.embedding_model(task)
            params = self.config.models[candidate].params
            keys = [embed_key(candidate, params, input_version, text) for text in asked]
            found = self._vectors_from_cache(
                keys, dimensions, document_key, self.config.models[candidate].int8
            )
            cached = sum(1 for key in keys if key in found)
            if cached:
                hit_keys = [key for key in keys if key in found]
                self.log.append(
                    self._record(
                        task, candidate, input_version, batch_key(hit_keys), "hit", document_key
                    ).model_copy(update={"items": cached})
                )
            missing = {key: text for key, text in zip(keys, asked, strict=True) if key not in found}
            tokens, latency = 0, 0
            cost: float | None = 0.0
            if missing:
                vectors, tokens, cost, latency = self._embed_call(
                    task, candidate, dimensions, input_version, missing, document_key
                )
                found.update(vectors)
            return EmbedReply(
                vectors=tuple(found[key] for key in keys),
                task=task,
                candidate=candidate,
                dimensions=dimensions,
                cached=cached,
                sent=len(missing),
                input_tokens=tokens,
                cost_usd=cost,
                latency_ms=latency,
                keys=tuple(keys),
            )

    def _vectors_from_cache(
        self, keys: Sequence[str], dimensions: int, document_key: str | None, int8: bool
    ) -> dict[str, tuple[float, ...]]:
        found: dict[str, tuple[float, ...]] = {}
        for key in dict.fromkeys(keys):
            entry = self.cache.get(key)
            vector = entry.reply.vector if entry is not None else None
            if vector is None or len(vector) != dimensions or _vector_error(vector, int8):
                continue
            found[key] = vector
            if document_key is not None:
                self.cache.tag(key, document_key)
        return found

    def _embed_call(
        self,
        task: str,
        candidate: str,
        dimensions: int,
        input_version: str,
        missing: Mapping[str, str],
        document_key: str | None,
    ) -> tuple[dict[str, tuple[float, ...]], int, float | None, int]:
        """Send the texts not in the cache, in one request; cache each vector it returns."""
        client = self._embedding_clients.get(candidate)
        key = batch_key(list(missing))
        reason = self._blocked(candidate, client, self.log.records())
        if reason is not None or client is None:
            step = Step(candidate=candidate, result=f"skipped: {reason or 'no client'}")
            raise NoModelAvailableError(task, [step], self._first_reset((candidate,)))
        price = self.config.models[candidate].price
        started = time.monotonic()
        try:
            reply = client.embed(list(missing.values()))
            vectors = _checked(reply, len(missing), dimensions, self.config.models[candidate].int8)
        except ModelCallError as exc:
            inputs = exc.usage[0] if exc.usage else 0
            unknown = exc.received_reply and exc.usage is None and not price.free
            self.log.append(
                self._record(
                    task, candidate, input_version, key, exc.kind, document_key
                ).model_copy(
                    update={
                        "latency_ms": _ms_since(started),
                        "error": exc.detail,
                        "input_tokens": inputs,
                        "cost_usd": None if unknown else price.cost(inputs, 0),
                        "items": len(missing),
                    }
                )
            )
            if exc.kind == "invalid":
                raise InvalidModelRequestError(task, candidate, exc.detail) from exc
            step = Step(candidate=candidate, result=f"{exc.kind}: {exc.detail}")
            raise NoModelAvailableError(task, [step], self._first_reset((candidate,))) from exc
        latency = _ms_since(started)
        cost = price.cost(reply.input_tokens, 0) if reply.usage_reported or price.free else None
        self.log.append(
            self._record(task, candidate, input_version, key, "ok", document_key).model_copy(
                update={
                    "input_tokens": reply.input_tokens,
                    "cost_usd": cost,
                    "latency_ms": latency,
                    "items": len(missing),
                }
            )
        )
        out: dict[str, tuple[float, ...]] = {}
        for text_key, vector in zip(missing, vectors, strict=True):
            entry = ModelReply(
                text="",
                vector=vector,
                usage_reported=reply.usage_reported,
                served_model=reply.served_model,
            )
            self.cache.put(
                text_key,
                CachedReply(candidate=candidate, reply=entry, created_at=self._clock()),
                document_key,
            )
            out[text_key] = vector
        return out, reply.input_tokens, cost, latency

    # ------------------------------------------------------------------- guards

    def _blocked(
        self,
        candidate: str,
        client: ModelClient | EmbeddingClient | None,
        records: list[CallRecord],
    ) -> str | None:
        """Why ``candidate`` must not be called now, or ``None`` (maybe after a short wait)."""
        if client is None:
            return "no client was built for it"
        missing = client.check()
        if missing is not None:
            return missing
        spec = self.config.models[candidate]
        if not spec.price.free:
            spent = spent_usd(records)
            if spent is None:
                return "paid calls paused pending cost reconciliation"
            if spent >= self.max_spend_usd:
                return f"spend cap reached (${spent:.4f} of ${self.max_spend_usd:.2f})"
        if spec.quota is None:
            return None
        quota = self.config.quotas[spec.quota]
        now = self._clock()
        if quota.per_day is not None:
            used = len(requests_since(records, spec.quota, day_start(now, quota.resets)))
            if used >= quota.per_day:
                reset = next_reset(now, quota.resets).astimezone(UTC)
                return (
                    f"daily quota {spec.quota} used ({used}/{quota.per_day}), "
                    f"resets {reset:%Y-%m-%d %H:%M} UTC"
                )
        limited = [
            r
            for r in requests_since(
                records, spec.quota, now - timedelta(seconds=self.config.cooldown_s)
            )
            if r.outcome == "rate_limited"
        ]
        if limited:
            ago = int((now - limited[-1].at).total_seconds())
            return (
                f"rate-limited {ago}s ago; quota {spec.quota} rests {int(self.config.cooldown_s)}s"
            )
        return self._wait_for_minute(spec.quota, quota.per_minute, records)

    def _wait_for_minute(
        self, quota: str, per_minute: int | None, records: list[CallRecord]
    ) -> str | None:
        if per_minute is None:
            return None
        now = self._clock()
        window = requests_since(records, quota, now - MINUTE)
        if len(window) < per_minute:
            return None
        if per_minute == 0:
            return f"per-minute limit of quota {quota} is 0"
        oldest = sorted(r.at for r in window)[len(window) - per_minute]
        wait = (oldest + MINUTE - now).total_seconds()
        if wait > self.config.max_wait_s:
            return f"per-minute limit of quota {quota} ({per_minute}/min): next slot in {wait:.0f}s"
        self._sleep(max(wait, 0.0))
        return None

    def _first_reset(self, chain: Sequence[str]) -> datetime | None:
        now = self._clock()
        resets = [
            next_reset(now, self.config.quotas[q].resets).astimezone(UTC)
            for c in chain
            if (q := self.config.models[c].quota) is not None
            and self.config.quotas[q].per_day is not None
        ]
        return min(resets) if resets else None

    def _record(
        self,
        task: str,
        candidate: str,
        prompt_version: str,
        key: str,
        outcome: Outcome,
        document_key: str | None,
    ) -> CallRecord:
        return CallRecord(
            at=self._clock(),
            task=task,
            candidate=candidate,
            prompt_version=prompt_version,
            key=key,
            outcome=outcome,
            quota=self.config.models[candidate].quota,
            document_key=document_key,
        )


def _ms_since(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _vector_error(vector: Sequence[float], int8: bool) -> str | None:
    if not any(vector):
        return "returned an all-zero vector"
    if int8 and not all(isinstance(v, int) and -128 <= v <= 127 for v in vector):
        return "returned a component outside signed int8"
    if not all(math.isfinite(v) for v in vector):
        return "returned a vector with a non-finite number"
    return None


def _checked(
    reply: EmbeddingReply, texts: int, dimensions: int, int8: bool
) -> list[tuple[float, ...]]:
    """The reply's vectors, if there is one per text, each of the declared size, all finite.

    A wrong count or a broken number is the provider's fault (``unavailable``); a
    wrong size means the configuration names the wrong model or size (``invalid``:
    it would fail again, so the call stops).
    """
    if len(reply.vectors) != texts:
        raise ModelCallError(
            "unavailable",
            f"returned {len(reply.vectors)} vectors for {texts} texts",
            received_reply=True,
        )
    out: list[tuple[float, ...]] = []
    for vector in reply.vectors:
        if len(vector) != dimensions:
            raise ModelCallError(
                "invalid",
                f"returned {len(vector)} numbers per vector; the configuration says {dimensions}",
                received_reply=True,
            )
        error = _vector_error(vector, int8)
        if error:
            raise ModelCallError(
                "unavailable",
                error,
                received_reply=True,
                usage=(reply.input_tokens, 0) if reply.usage_reported else None,
            )
        out.append(vector)
    return out
