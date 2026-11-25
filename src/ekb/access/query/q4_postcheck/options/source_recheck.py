"""``q4_postcheck: source_recheck``: ask the source before releasing anything (S14).

For one question, one person and Q3's hits, best first:

1. **Nothing for no one.** A scope that reads nothing (unknown person, leaver)
   or no hits: nothing to check, nothing released.
2. **One check per document.** The hits' documents, each once, go to the
   :class:`~ekb.access.query.q4_postcheck.port.SourceAccessCheck` with the
   person's user ID. Five chunks of one page are one question to the source.
3. **Hold back anything but a yes.** A hit whose document is denied, deleted
   or unanswered is held back (:class:`~ekb.contracts.Withheld`). So is one the
   check forgot to answer, or answered for another document, and every hit if
   the check itself fails: unknown is no.
4. **Remove personal data, then cite.** Each released chunk's text, section
   trail and title go through the redactor; the passages are numbered 1, 2, 3 …
   in rank order. A citation is marked ``source_changed`` when the source's
   content hash differs from the one the chunk was indexed from (the text is
   released: access is confirmed, and the worker is about to replace it).

Q3's order is kept, and nothing is added back to replace what was held back:
the answer then simply has fewer passages (card 4.7 measures how many it needs).
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

from ekb.access.query.q4_postcheck.port import Redacted, Redactor, SourceAccessCheck
from ekb.contracts import (
    AccessVerdict,
    Citation,
    Hit,
    Passage,
    PiiKind,
    Redaction,
    Released,
    Scope,
    SourceAccess,
    Withheld,
)
from ekb.contracts.events import parse_version
from ekb.platform.registry import register

logger = logging.getLogger(__name__)

VERSION = 1


@register("q4_postcheck", "source_recheck")
class SourceRecheck:
    def __init__(self, access: SourceAccessCheck, redactor: Redactor) -> None:
        self._access = access
        self._redactor = redactor

    @property
    def postcheck_id(self) -> str:
        return (
            f"source_recheck@{VERSION}(access={self._access.checker_id}, "
            f"redact={self._redactor.redactor_id})"
        )

    def release(self, question: str, scope: Scope, hits: Sequence[Hit]) -> Released:
        empty = Released(postcheck_id=self.postcheck_id)
        if scope.principals is None or not hits:
            return empty
        keys = tuple(dict.fromkeys(hit.chunk.document_key for hit in hits))
        answers = self._check(scope.user_id, keys)
        checked = {key: self._answer_for(key, answers) for key in keys}
        for hit in hits:
            key = hit.chunk.document_key
            access = checked[key]
            if access.verdict.released:
                indexed = parse_version(hit.chunk.document_version)
                if indexed is None or access.first_rev is None:
                    checked[key] = self._unavailable(
                        key, "the indexed lifetime could not be checked"
                    )
                elif indexed[0] < access.first_rev:
                    checked[key] = SourceAccess(
                        document_key=key,
                        verdict=AccessVerdict.DELETED,
                        reason="the indexed page was deleted before this page was created",
                        checker_id=self._access.checker_id,
                    )
        passages: list[Passage] = []
        withheld: list[Withheld] = []
        for hit in hits:
            access = checked[hit.chunk.document_key]
            if access.verdict.released:
                passages.append(self._passage(len(passages) + 1, hit, access))
            else:
                withheld.append(Withheld(hit=hit, access=access))
        unchecked = sum(1 for w in withheld if w.access.verdict is AccessVerdict.UNAVAILABLE)
        return empty.model_copy(
            update={
                "passages": tuple(passages),
                "withheld": tuple(withheld),
                "checked": tuple(checked.values()),
                "unchecked": unchecked,
                "notes": _notes(unchecked, passages),
            }
        )

    def _check(self, user_id: str, keys: Sequence[str]) -> Mapping[str, SourceAccess]:
        try:
            return self._access.check(user_id, keys)
        except Exception as exc:  # fail closed: a broken check releases nothing
            logger.exception("Q4's access check failed; every passage is held back")
            reason = f"the access check failed ({type(exc).__name__}): held back"
            return {key: self._unavailable(key, reason) for key in keys}

    def _answer_for(self, key: str, answers: Mapping[str, SourceAccess]) -> SourceAccess:
        access = answers.get(key)
        if access is None:
            return self._unavailable(key, "the access check gave no answer for this document")
        if access.document_key != key:
            return self._unavailable(key, "the access check answered for another document")
        return access

    def _unavailable(self, key: str, reason: str) -> SourceAccess:
        return SourceAccess(
            document_key=key,
            verdict=AccessVerdict.UNAVAILABLE,
            reason=reason,
            checker_id=self._access.checker_id,
        )

    def _passage(self, number: int, hit: Hit, access: SourceAccess) -> Passage:
        chunk = hit.chunk
        text = self._redactor.redact(chunk.text)
        trail = tuple(self._redactor.redact(heading) for heading in chunk.section)
        page = chunk.document_key.partition(":")[2] or chunk.document_key
        title = self._redactor.redact(access.title or page)
        changed = (
            access.content_hash is not None
            and chunk.source_content_hash is not None
            and access.content_hash != chunk.source_content_hash
        )
        citation = Citation(
            number=number,
            document_key=chunk.document_key,
            title=title.text,
            section=tuple(heading.text for heading in trail),
            chunk_id=chunk.chunk_id,
            document_version=chunk.document_version,
            source_changed=changed,
        )
        return Passage(
            citation=citation,
            text=text.text,
            redactions=_total((text, title, *trail)),
        )


def _total(parts: Iterable[Redacted]) -> tuple[Redaction, ...]:
    """The redactions of a passage's text, trail and title, added up per kind."""
    counts: Counter[PiiKind] = Counter()
    for part in parts:
        for redaction in part.redactions:
            counts[redaction.kind] += redaction.count
    return tuple(Redaction(kind=kind, count=counts[kind]) for kind in PiiKind if kind in counts)


def _notes(unchecked: int, passages: Sequence[Passage]) -> tuple[str, ...]:
    """What the asker may be told: never which document was held back, or why."""
    notes: list[str] = []
    if unchecked:
        what = "1 passage" if unchecked == 1 else f"{unchecked} passages"
        notes.append(
            f"{what} could not be checked against the source, which did not answer: "
            "the answer may be incomplete"
        )
    changed = [p.citation.number for p in passages if p.citation.source_changed]
    if changed:
        cited = ", ".join(f"[{n}]" for n in changed)
        notes.append(
            f"the source of {cited} has changed since it was indexed: the passage may be "
            "out of date until it is indexed again"
        )
    return tuple(notes)
