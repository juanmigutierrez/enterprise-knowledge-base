"""Builders for T3 tests: a file to parse, and the docs tool's page JSON around it."""

from __future__ import annotations

import json

from ekb.process.t3_parse.port import MARKDOWN, SourceFile


def source_file(
    body: str | bytes, media_type: str = MARKDOWN, title: str = "Annual leave policy"
) -> SourceFile:
    return SourceFile(
        document_key="hr_docs:HR-PL-01",
        version="1.1",
        title=title,
        language="en",
        media_type=media_type,
        content=body.encode() if isinstance(body, str) else body,
    )


def page_json(body: str, **extra: object) -> bytes:
    """What the docs tool sends for ``GET /pages/{id}``: metadata plus the Markdown body."""
    page: dict[str, object] = {
        "id": "HR-PL-01",
        "title": "Annual leave policy",
        "language": "en",
        "department": "people",
        "acl": {"users": [], "groups": ["all-staff"]},
        "rev": 1,
        "acl_rev": 1,
        "content_hash": "sha256:x",
        "updated_at": "2026-11-24T09:00:00Z",
        "body": body,
    }
    return json.dumps(page | extra).encode()
