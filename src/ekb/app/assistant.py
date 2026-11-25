"""The HR assistant behind the MCP tools (S15): the query path, a written answer, and reuse.

``ekb.access.mcp`` calls this with the caller the token named; nothing here
trusts a user ID from anywhere else. Three things a tool can ask for:

- ``whoami``: Q2's scope for the caller, in plain words.
- ``search``: Q1 to Q4 (S14, :class:`~ekb.app.answer.KnowledgeQuery`) for the
  caller: the cited passages, S14's :class:`~ekb.contracts.Answer`.
- ``ask``: the same passages, turned into a short answer by the configured
  writer (``model``: the gateway's ``answer`` task; ``extractive`` offline)::

      (caller, question) ─▶ answer cache: same agent, person, groups, question, options?
          hit  ─▶ the asker's readable index unchanged?  ─▶ Q4 again, for these hits, now
                    every cited page confirmed, same content, same passages ─▶ reuse
                    anything else ─▶ drop it, answer afresh
          miss ─▶ Q1 → Q4 ─▶ nothing released? fixed sentence, no writer
                            ─▶ writer (released passages only) ─▶ keep it if complete

**Reuse never skips Q4** (D16 #10, D8): a cached answer is served only after the
source has confirmed, for the person asking now, every page it was written
from, at the same content version, and only if Q4 would release exactly the
same passages. A revoked reader, a group removed in the identity provider, a
deleted page, an edited page or a source that does not answer: the cached
answer is dropped and the question answered afresh, which then leaves out
what Q4 holds back. What made it stale is for the demo and the log, never the
agent: the fresh answer simply does not mention the page.

**Only complete answers are kept:** at least one passage, every passage
checked, written by the configured writer (not its fallback), with no group or
index change during the query. The key is per person and agent, never shared:
someone else's search may find different passages.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ekb.access.answer.port import NOTHING_FOUND, AnswerCache, AnswerWriter, CachedAnswer, Draft
from ekb.access.auth.port import TokenCheck
from ekb.app.answer import KnowledgeQuery
from ekb.app.choices import MODEL_OPTIONS, Choices
from ekb.contracts import (
    AccessVerdict,
    Answer,
    AnswerResult,
    AssistantAnswer,
    Caller,
    Citation,
    Identity,
    Passage,
    Released,
    Scope,
    SourceAccess,
)
from ekb.platform import registry
from ekb.platform.gateway.port import Completer

logger = logging.getLogger(__name__)


def reused_note(pages: int) -> str:
    """Agent-safe: what a reused answer says about itself."""
    return (
        f"a cached answer, reused after the source confirmed its {pages} "
        f"page{'s' if pages != 1 else ''} for you just now"
    )


@dataclass(frozen=True)
class AssistantResult:
    """One ``ask``: what the agent gets, and how it came about (for the demo and the log)."""

    reply: AssistantAnswer
    result: AnswerResult | None = None
    """The query path's trace when answered afresh; ``None`` when a cached answer was reused."""
    recheck: Released | None = None
    """Q4's fresh answer for a cached answer's hits, when there was one."""
    discarded: str = ""
    """Why a cached answer was not reused, in plain words. Never sent to the agent."""
    kept: bool = False
    """Whether this answer was kept for reuse."""

    @property
    def reused(self) -> bool:
        return self.reply.cached


@dataclass(frozen=True)
class Assistant:
    """The Knowledge API's service: what the MCP tools call (``KnowledgeService``)."""

    knowledge: KnowledgeQuery
    writer: AnswerWriter
    cache: AnswerCache
    on_answer: Callable[[Caller, AssistantResult], None] | None = None
    """Told about every ``ask``, with how it came about: for the demo, never the agent."""

    def whoami(self, caller: Caller) -> Identity:
        scope = self.knowledge.search.q2.scope(caller.user_id)
        groups = tuple(sorted(scope.principals.groups)) if scope.principals is not None else ()
        return Identity(
            user_id=caller.user_id,
            agent_id=caller.agent_id,
            reads_as=scope.reason,
            groups=groups,
            reads_nothing=scope.reads_nothing,
        )

    def search(self, caller: Caller, question: str) -> Answer:
        answer = self.knowledge.ask(caller.user_id, question).answer
        logger.info("search by %s: %d passages", caller.token_id, len(answer.passages))
        return answer.model_copy(update={"agent_id": caller.agent_id})

    def ask(self, caller: Caller, question: str) -> AssistantAnswer:
        return self.answer(caller, question).reply

    @property
    def options(self) -> tuple[str, ...]:
        """Everything that shapes an answer: Q1 to Q4 and the writer. Part of the cache key."""
        knowledge = self.knowledge
        return (
            knowledge.q1.planner_id,
            knowledge.search.q2.prefilter_id,
            knowledge.search.q3.retriever_id,
            knowledge.q4.postcheck_id,
            self.writer.writer_id,
        )

    def answer(self, caller: Caller, question: str) -> AssistantResult:
        scope = self.knowledge.search.q2.scope(caller.user_id)
        key = cache_key(caller, scope, question, self.options)
        entry = self.cache.get(key)
        recheck: Released | None = None
        discarded = ""
        if entry is not None:
            if not entry.still_current():
                discarded = "what the person may read changed in the index since it was written"
            else:
                recheck = self.knowledge.q4.release(question, scope, entry.hits)
                discarded = stale(entry.passages, entry.checked, recheck)
                if not discarded and (
                    not entry.still_current()
                    or self.knowledge.search.q2.scope(caller.user_id).principals != scope.principals
                ):
                    discarded = "what the person may read changed during the source check"
            if not discarded:
                reply = self._reused(caller, question, entry, recheck)
                logger.info("ask by %s: reused a cached answer", caller.token_id)
                return self._told(caller, AssistantResult(reply=reply, recheck=recheck))
            self.cache.drop(key)
            logger.info("ask by %s: cached answer dropped: %s", caller.token_id, discarded)
        snapshot = self.knowledge.index.ranking_snapshot(scope.principals)
        result = self.knowledge.ask(caller.user_id, question)
        passages = result.answer.passages
        draft = self.writer.write(question, passages) if passages else None
        if draft is not None:
            contributing = {p.citation.chunk_id for p in passages}
            hits = tuple(hit for hit in result.hits if hit.chunk.chunk_id in contributing)
            final = self.knowledge.q4.release(question, result.scope, hits)
            current = self.knowledge.search.q2.scope(caller.user_id)
            if (
                stale(passages, result.released.checked, final)
                or current.principals != result.scope.principals
                or result.scope.principals != scope.principals
                or not snapshot.is_current()
            ):
                withheld = AssistantAnswer(
                    question=question,
                    user_id=caller.user_id,
                    agent_id=caller.agent_id,
                    answer="I could not confirm this answer for you. Please try again.",
                )
                return self._told(
                    caller, AssistantResult(reply=withheld, result=result, recheck=final)
                )
        reply = _reply(caller, question, result.answer, draft)
        complete = (
            draft is not None
            and not draft.fallback
            and result.released.unchecked == 0
            and result.scope.principals == scope.principals
            and snapshot.is_current()
        )
        if complete and draft is not None:
            self.cache.put(
                key,
                CachedAnswer(
                    question=question,
                    scope=scope,
                    hits=result.hits,
                    passages=passages,
                    checked=result.released.checked,
                    draft=draft,
                    searches=result.answer.searches,
                    notes=result.answer.notes,
                    options=reply.options,
                    still_current=snapshot.is_current,
                ),
            )
        logger.info(
            "ask by %s: %d passages, %s",
            caller.token_id,
            len(passages),
            "kept for reuse" if complete else "not kept",
        )
        return self._told(
            caller,
            AssistantResult(
                reply=reply, result=result, recheck=recheck, discarded=discarded, kept=complete
            ),
        )

    def _told(self, caller: Caller, outcome: AssistantResult) -> AssistantResult:
        if self.on_answer is not None:
            self.on_answer(caller, outcome)
        return outcome

    def _reused(
        self, caller: Caller, question: str, entry: CachedAnswer, recheck: Released | None
    ) -> AssistantAnswer:
        passages = recheck.passages if recheck is not None else entry.passages
        pages = len({p.citation.document_key for p in passages})
        return AssistantAnswer(
            question=question,
            user_id=caller.user_id,
            agent_id=caller.agent_id,
            answer=entry.draft.text,
            citations=_cited(passages, entry.draft.cited),
            notes=tuple(dict.fromkeys((*entry.notes, *entry.draft.notes, reused_note(pages)))),
            cached=True,
            options=entry.options,
        )


def _reply(caller: Caller, question: str, answer: Answer, draft: Draft | None) -> AssistantAnswer:
    text = draft.text if draft is not None else NOTHING_FOUND
    cited = draft.cited if draft is not None else ()
    notes = (*answer.notes, *(draft.notes if draft is not None else ()))
    writer = (draft.writer_id,) if draft is not None else ()
    return AssistantAnswer(
        question=question,
        user_id=caller.user_id,
        agent_id=caller.agent_id,
        answer=text,
        citations=_cited(answer.passages, cited),
        notes=tuple(dict.fromkeys(notes)),
        options=(*answer.options, *writer),
    )


def _cited(passages: Sequence[Passage], numbers: Sequence[int]) -> tuple[Citation, ...]:
    wanted = set(numbers)
    return tuple(p.citation for p in passages if p.citation.number in wanted)


def stale(passages: Sequence[Passage], checked: Sequence[SourceAccess], recheck: Released) -> str:
    """Why these passages may not be released, given Q4's answer for its hits now; ``""`` if it may.

    For the demo and the log: it names pages, so it never reaches the agent.
    """
    if recheck.unchecked:
        count = recheck.unchecked
        return f"{count} passage{'s' if count != 1 else ''} could not be checked against the source"
    now = {access.document_key: access for access in recheck.checked}
    then = {access.document_key: access for access in checked}
    for key in dict.fromkeys(p.citation.document_key for p in passages):
        page = key.partition(":")[2] or key
        access = now.get(key)
        if access is None or access.verdict is not AccessVerdict.ALLOWED:
            verdict = access.verdict.value if access is not None else "unchecked"
            reason = f" ({access.reason})" if access is not None and access.reason else ""
            return f"{page} is {verdict} now{reason}"
        before = then.get(key)
        if before is None or not access.content_hash or before.content_hash != access.content_hash:
            return f"{page} changed in the source since the answer was written"
    if recheck.passages != passages:
        return "Q4 would release other passages now"
    return ""


def cache_key(caller: Caller, scope: Scope, question: str, options: Sequence[str]) -> str:
    """One person, one agent, their groups now, the question (spacing and case aside), the
    options. Hashed, so the key holds no question text."""
    groups = sorted(scope.principals.groups) if scope.principals is not None else None
    body = {
        "agent": caller.agent_id,
        "user": caller.user_id,
        "groups": groups,
        "question": " ".join(question.split()).casefold(),
        "options": list(options),
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def build_assistant(
    choices: Choices,
    knowledge: KnowledgeQuery,
    gateway: Completer | None = None,
    on_answer: Callable[[Caller, AssistantResult], None] | None = None,
) -> Assistant:
    """The writer and the cache ``choices`` (from :func:`~ekb.app.choices.choose_assistant`)
    name, around ``knowledge``. A model writer needs ``gateway``."""
    option = choices.option("answer")
    params = choices.params("answer")
    if option in MODEL_OPTIONS["answer"]:
        if gateway is None:
            raise ValueError(f"answer: {option!r} calls a model; it needs the model gateway")
        params["gateway"] = gateway
    writer = registry.create("answer", option, **params)
    if not isinstance(writer, AnswerWriter):
        raise TypeError(f"answer option {option!r} is not an AnswerWriter")
    cache = registry.create(
        "answer_cache", choices.option("answer_cache"), **choices.params("answer_cache")
    )
    if not isinstance(cache, AnswerCache):
        raise TypeError(f"answer_cache option {choices.option('answer_cache')!r} is not a cache")
    return Assistant(knowledge=knowledge, writer=writer, cache=cache, on_answer=on_answer)


def build_tokens(choices: Choices) -> TokenCheck:
    """The token check ``choices`` names (``static_tokens``: from the file in its params)."""
    tokens = registry.create("auth", choices.option("auth"), **choices.params("auth"))
    if not isinstance(tokens, TokenCheck):
        raise TypeError(f"auth option {choices.option('auth')!r} is not a TokenCheck")
    return tokens
