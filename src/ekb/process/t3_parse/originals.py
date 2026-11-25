"""Opening a stored original to find the document inside it.

The raw store keeps exactly what each source sent (S3). For the docs tool
that is its API's JSON for one page: the Markdown body sits in a ``body``
field, next to the title, language, permissions and revision numbers. Other
tools differ: a SharePoint connector would store the ``.docx`` itself, with
its title in the event's metadata.

So T3 has two parts. An **original reader** (here) knows one source's
envelope and returns the file inside it as a
:class:`~ekb.process.t3_parse.port.SourceFile`; a **parser** (``options/``)
knows one file format. Every parser then works on the same input, and adding
a source never touches the parsers.

Which reader goes with which source is decided in the wiring
(``ekb.app.parse``), not guessed from the bytes. A source with no reader is
not a document source: its items are structured records (HR records,
database rows, identity users), which are queried, not parsed and embedded
(D7).

Like the connector's own client (``ekb.connect.docs_api``), a reader keeps
its own copy of the few fields it needs and ignores the rest: a source adding
a field must not break parsing.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, ValidationError

from ekb.process.t3_parse.port import MARKDOWN, ParseError, SourceFile

OriginalReader = Callable[[str, str, bytes], SourceFile]
"""``(document_key, version, original bytes) -> SourceFile``; raises :class:`ParseError`."""


class _DocsPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    title: str
    language: str | None = None
    department: str | None = None
    body: str


def docs_page(document_key: str, version: str, original: bytes) -> SourceFile:
    """The docs tool's page JSON (``GET /pages/{id}``) → its Markdown body and metadata."""
    try:
        page = _DocsPage.model_validate_json(original)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "the page"
        raise ParseError(
            f"{document_key} v{version}: not a docs tool page ({where}: {first['msg']})"
        ) from exc
    return SourceFile(
        document_key=document_key,
        version=version,
        title=page.title,
        language=page.language,
        media_type=MARKDOWN,
        content=page.body.encode("utf-8"),
        department=page.department,
    )
