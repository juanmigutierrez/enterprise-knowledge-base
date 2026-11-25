"""``q4_redact: none``: release the text as indexed.

The baseline a redactor is measured against, and a choice for sources that hold
no personal data. Not the default: ``rules`` is (``ekb.app.choices``).
"""

from __future__ import annotations

from ekb.access.query.q4_postcheck.port import Redacted
from ekb.platform.registry import register

VERSION = 1


@register("q4_redact", "none")
class NoRedactor:
    @property
    def redactor_id(self) -> str:
        return f"none@{VERSION}"

    def redact(self, text: str) -> Redacted:
        return Redacted(text)
