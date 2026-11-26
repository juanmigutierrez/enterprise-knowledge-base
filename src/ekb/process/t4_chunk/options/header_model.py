"""T4 header option ``model``: a language model writes the headers, one call per document.

The model is whatever the gateway routes the ``context_header`` task to (D15):
Gemini Flash-Lite, then gpt-oss-20b on Groq, Mistral Small and OpenRouter's free
router in the POC; Claude Haiku 5.5 in the production design. This option never
names a model: it names the task.

**One call per document, not per chunk** (D16 #4). The model reads the whole
document once, then the numbered chunks, and answers with JSON holding one
header per chunk::

    {"chunk_0": "From the Annual leave policy: who the policy covers ...",
     "chunk_1": "..."}

One key per chunk, every key required: the schema itself says how many headers
are expected, so a model that forgets one is caught by the provider (structured
output) or here.

**Cached and forgotten through the gateway.** The call's cache key holds the
document's text, so an unchanged document costs no request the next time (D16
#1), and an edited one gets new headers. The reply is tagged with the document
key: when T2 purges the document, ``gateway.forget`` drops it, because a header
paraphrases the document's text (S8 wires that).

**Never blocks the document.** If no model is available (no key, every free
quota used up), the request is rejected, or the reply doesn't parse, every
header is left empty and :attr:`Headers.note` says why. A chunk without a header
is still a correct chunk; one that never gets processed is a freshness problem.
"""

from __future__ import annotations

import json
from typing import Any

from ekb.contracts import Chunk, ParsedDocument
from ekb.platform.gateway.errors import GatewayError
from ekb.platform.gateway.port import Completer
from ekb.platform.registry import register
from ekb.process.t4_chunk.header import NO_HEADER, HeaderOutcome, Headers, clean

VERSION = 1
PROMPT_VERSION = "header@1"
"""Part of the cache key. Bump it when the prompt or the way the reply is read changes."""
TASK = "context_header"
MAX_WORDS = 50
LANGUAGES = {"en": "English", "it": "Italian", "es": "Spanish"}

SYSTEM = """\
You write context headers for the passages of one company document, so that a search \
engine can find each passage. For every passage, write one or two sentences, at most \
{max_words} words, saying which document the passage is from and what it is about, in \
the words an employee would search with. Name the subject, and the country or office \
when the document gives one. Say what words such as "this policy" or "the employee" \
refer to. Use only facts that are in the document. Write in {language}. Reply with \
JSON only: one key per passage, chunk_0 to chunk_{last}."""


def key(position: int) -> str:
    return f"chunk_{position}"


@register("t4_header", "model")
class ModelHeaderWriter:
    def __init__(self, gateway: Completer, *, task: str = TASK, max_words: int = MAX_WORDS) -> None:
        self._gateway = gateway
        self._task = task
        self._max_words = max_words

    @property
    def header_id(self) -> str:
        return f"model@{VERSION}({PROMPT_VERSION})"

    def write(self, document: ParsedDocument, chunks: tuple[Chunk, ...]) -> Headers:
        if not chunks:
            return Headers((), self.header_id, HeaderOutcome.WRITTEN, "no chunks")
        try:
            reply = self._gateway.complete(
                self._task,
                prompt(document, chunks),
                system=system(document, len(chunks), self._max_words),
                prompt_version=PROMPT_VERSION,
                max_output_tokens=max_output_tokens(len(chunks)),
                json_schema=schema(len(chunks)),
                document_key=document.document_key,
            )
        except GatewayError as exc:
            return self._failed(f"no model could write them ({_short(str(exc))})", len(chunks))
        texts = self._read(reply.text, len(chunks))
        if texts is None:
            why = f"{reply.candidate}'s reply was not the JSON asked for"
            return self._failed(why, len(chunks))
        written_by = f"model@{VERSION}({PROMPT_VERSION}, {reply.candidate})"
        missing = sum(1 for text in texts if not text)
        how = "from the cache, no request" if reply.cached else "one request"
        note = f"written by {reply.candidate}, {how}"
        if missing:
            note += f"; {missing} of {len(texts)} headers missing from the reply"
        outcome = HeaderOutcome.CACHED if reply.cached else HeaderOutcome.WRITTEN
        return Headers(texts, written_by, outcome, note)

    def _read(self, text: str, count: int) -> tuple[str, ...] | None:
        """One cleaned header per chunk, ``""`` where the reply has none; ``None`` if
        the reply is not a JSON object at all."""
        try:
            data = json.loads(text)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        values = (data.get(key(position)) for position in range(count))
        return tuple(
            clean(value, self._max_words) if isinstance(value, str) else "" for value in values
        )

    def _failed(self, why: str, count: int) -> Headers:
        return Headers(
            texts=("",) * count,
            header_id=f"model@{VERSION}({PROMPT_VERSION}, {NO_HEADER})",
            outcome=HeaderOutcome.FAILED,
            note=f"no header: {why}",
        )


def system(document: ParsedDocument, count: int, max_words: int) -> str:
    language = LANGUAGES.get(document.language or "", "the language the document is written in")
    return SYSTEM.format(max_words=max_words, language=language, last=count - 1)


def prompt(document: ParsedDocument, chunks: tuple[Chunk, ...]) -> str:
    """The document once, then the passages, each tagged with its key."""
    passages = "\n\n".join(
        f"<{key(chunk.position)}>\n{chunk.text}\n</{key(chunk.position)}>" for chunk in chunks
    )
    return (
        f'<document title="{document.title}">\n{document.text}\n</document>\n\n'
        f"Write a header for each of these {len(chunks)} passages of the document:\n\n"
        f"{passages}"
    )


def schema(count: int) -> dict[str, Any]:
    """One required string per chunk. No ``additionalProperties``: not every
    provider's structured output accepts it, and extra keys are ignored here."""
    return {
        "title": "context_headers",
        "description": "One context header per passage.",
        "type": "object",
        "properties": {
            key(position): {"type": "string", "description": f"The header of passage {position}."}
            for position in range(count)
        },
        "required": [key(position) for position in range(count)],
    }


def max_output_tokens(count: int) -> int:
    """Room for every header plus the JSON around them, and some for models that think
    before answering (their thinking counts as output). Capped (D16 #7)."""
    return min(4096, 256 + 96 * count)


def _short(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
