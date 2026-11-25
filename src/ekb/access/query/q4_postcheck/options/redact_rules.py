"""``q4_redact: rules``: remove the personal data that identifies a person or their money.

Removed from every released passage, for every asker: IBANs, payment card
numbers, Italian fiscal codes and Spanish DNI / NIE numbers, the high-risk kinds
of :class:`~ekb.contracts.PiiKind`. Each becomes a marker that says what was
there, ``[IBAN removed]``, so a reader knows something was taken out.

Kept: email addresses and phone numbers. An HR policy that gives the payroll
team's address must stay usable; T5 flags them without raising the chunk's
sensitivity for the same reason. People's names are not found by patterns at all
(Presidio, a later option, recognizes them).

The patterns are T5's own (``ekb.platform.pii``), so Q4 removes what T5 flags,
and the check digits keep an order number or a salary figure from being taken
for an IBAN.
"""

from __future__ import annotations

from collections import Counter

from ekb.access.query.q4_postcheck.port import Redacted
from ekb.contracts import PiiKind, Redaction
from ekb.platform import pii
from ekb.platform.registry import register

VERSION = 1

MARKERS = {
    PiiKind.IBAN: "[IBAN removed]",
    PiiKind.PAYMENT_CARD: "[card number removed]",
    PiiKind.IT_FISCAL_CODE: "[fiscal code removed]",
    PiiKind.ES_NATIONAL_ID: "[ID number removed]",
}
"""What replaces each kind removed: exactly the high-risk kinds."""


@register("q4_redact", "rules")
class RulesRedactor:
    @property
    def redactor_id(self) -> str:
        return f"rules@{VERSION}"

    def redact(self, text: str) -> Redacted:
        found = sorted(
            (item for item in pii.find(text) if item.kind in MARKERS), key=lambda f: f.start
        )
        if not found:
            return Redacted(text)
        pieces: list[str] = []
        cursor = 0
        for item in found:
            pieces += [text[cursor : item.start], MARKERS[item.kind]]
            cursor = item.end
        pieces.append(text[cursor:])
        counts = Counter(item.kind for item in found)
        redactions = tuple(
            Redaction(kind=kind, count=counts[kind]) for kind in PiiKind if kind in counts
        )
        return Redacted("".join(pieces), redactions)
