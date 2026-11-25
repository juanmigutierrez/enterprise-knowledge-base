"""T5's rules (S8): department, sensitivity and PII without a model.

Shared by the ``rules`` option and by ``model_plus_rules``, which starts from
these labels and only makes them stricter.

**Department.** The source's own label wins: the docs tool files every page
under a department, and the person who filed it knew. Only when a source has no
label are keywords counted in the title (three times) and the text; the
department with the most matches wins, and a tie means "no department".

**Sensitivity.** Explicit markers only, never topic words:

- a document that says it is restricted ("Restricted to the compensation team",
  "Do not forward", "(restricted)" in its title, and the Italian and Spanish
  equivalents) is ``restricted``, every chunk of it: the line usually sits at the
  top, and the sections below it are just as restricted;
- a document marked confidential ("(confidential)" in the title, a line starting
  "Confidential:") is ``confidential``, every chunk of it;
- a chunk holding high-risk personal data (an IBAN, a payment card, a codice
  fiscale, a DNI / NIE) is at least ``confidential``, that chunk only;
- everything else is ``internal``. ``public`` is never guessed: something leaves
  the company only when a source says so.

Topic words don't count, on purpose. The code of conduct says shopper data "are
confidential", and the remote work policy mentions the salary; neither is a
confidential document. A word list would label them so, and the friction of
over-labelling grows with every document (card 4.9 weighs both kinds of error).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from ekb.contracts import Chunk, ParsedDocument, Sensitivity
from ekb.process.t5_classify import pii
from ekb.process.t5_classify.port import DEPARTMENTS, Labels, passage

VERSION = 1

_RESTRICTED = (
    r"[(\[]\s*(?:restricted|riservat[oa]|restringid[oa])\s*[)\]]",  # "(restricted)" in a title
    r"\brestricted to\b",
    r"\b(?:accesso )?riservat[oa] (?:a|al|alla|ai|agli|alle)\b",
    r"\b(?:acceso )?restringid[oa] al?\b",
    r"\bdo not (?:forward|share|distribute)\b",
    r"\bnon (?:inoltrare|condividere|divulgare)\b",
    r"\bno (?:reenviar|reenvíe|compartir|distribuir|difundir)\b",
)
_CONFIDENTIAL = (
    r"[(\[]\s*(?:confidential|confidenziale|confidencial)\s*[)\]]",  # "(confidential)"
    r"^\s*(?:confidential|confidenziale|confidencial)\s*(?::|$)",  # a line "Confidential:"
)
MARKERS: tuple[tuple[Sensitivity, re.Pattern[str]], ...] = tuple(
    (level, re.compile(pattern, re.IGNORECASE | re.MULTILINE))
    for level, patterns in (
        (Sensitivity.RESTRICTED, _RESTRICTED),
        (Sensitivity.CONFIDENTIAL, _CONFIDENTIAL),
    )
    for pattern in patterns
)

DEPARTMENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "people": (
        "leave", "holiday", "vacation", "parental", "maternity", "paternity", "onboarding",
        "remote work", "code of conduct", "benefits", "welfare", "hiring", "ferie",
        "permessi", "congedo", "buoni pasto", "smart working", "assunzione", "vacaciones",
        "permiso", "teletrabajo", "nacimiento", "contratación",
    ),
    "finance": (
        "expense", "expenses", "reimbursement", "invoice", "receipt", "budget", "spese",
        "rimborso", "fattura", "nota spese", "gastos", "reembolso", "factura",
    ),
    "sales": (
        "retailer", "retailers", "deal", "pipeline", "account manager", "cliente", "clientes",
    ),
    "operations": ("flyer", "flyers", "volantino", "volantini", "folleto", "content processing"),
    "product": ("roadmap", "product requirements", "user research", "release notes"),
    "engineering": ("deployment", "repository", "code review", "on-call", "incident"),
    "data": ("dashboard", "data warehouse", "analytics", "kpi", "business intelligence"),
}  # fmt: skip
"""Fallback only, for sources with no department label of their own."""


@dataclass(frozen=True)
class Marker:
    level: Sensitivity
    text: str
    """The words that matched, as the document writes them."""


def markers(document: ParsedDocument) -> list[Marker]:
    """Every sensitivity marker in the document's title and text, strictest first."""
    found = [
        Marker(level, match.group(0).strip())
        for where in (document.title, document.text)
        for level, pattern in MARKERS
        for match in pattern.finditer(where)
    ]
    return sorted(found, key=lambda marker: -marker.level.rank)


def department(document: ParsedDocument) -> tuple[str | None, str]:
    """The document's department and where it came from, in plain words."""
    if document.department and document.department.strip():
        return document.department.strip().lower(), "from the source"
    title, text = document.title.lower(), document.text.lower()
    scores: Counter[str] = Counter()
    for name in DEPARTMENTS:
        for word in DEPARTMENT_KEYWORDS.get(name, ()):
            pattern = re.compile(rf"\b{re.escape(word)}\b")
            scores[name] += 3 * len(pattern.findall(title)) + len(pattern.findall(text))
    ranked = scores.most_common(2)
    if not ranked or ranked[0][1] == 0:
        return None, "none: the source has no label and no keyword matched"
    if len(ranked) > 1 and ranked[1][1] == ranked[0][1]:
        return None, f"none: keywords tie between {ranked[0][0]} and {ranked[1][0]}"
    return ranked[0][0], "from keywords (the source has no label)"


def labels(document: ParsedDocument, chunks: Sequence[Chunk]) -> tuple[tuple[Labels, ...], str]:
    """The rules' labels, one per chunk, and a note on the department."""
    name, origin = department(document)
    found = markers(document)
    floor = found[0] if found else None
    out: list[Labels] = []
    for chunk in chunks:
        kinds = pii.kinds(passage(chunk))
        level, why = Sensitivity.INTERNAL, "internal: no marker and no high-risk personal data"
        if floor is not None:
            level, why = floor.level, f"{floor.level}: the document says “{floor.text}”"
        risky = [kind.value for kind in kinds if kind.high_risk]
        if risky and Sensitivity.CONFIDENTIAL.rank > level.rank:
            level, why = Sensitivity.CONFIDENTIAL, f"confidential: {', '.join(risky)} in this chunk"
        out.append(Labels(sensitivity=level, department=name, pii=kinds, why=why))
    return tuple(out), f"department {name or 'none'} ({origin})"
