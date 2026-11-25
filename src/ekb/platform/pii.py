"""Finding personal data in text with patterns, for T5's labels and Q4's redaction.

Written for T5's rules (S8) and moved here in S14, so Q4 removes from released
text exactly what T5 flags on a chunk: the query steps and the processing steps
cannot import each other (import-linter), and two copies of these patterns would
drift apart. A pattern fixed here is fixed for labelling and redaction alike.

Each detector is a regular expression, and where the identifier carries a check
digit (IBAN, payment card, Spanish DNI / NIE), the check digit too. A check digit
turns "a long number" into "almost certainly an identifier": an order number or a
salary figure does not pass it by chance often enough to matter.

The detectors run in a fixed order, and each match is blanked out before the
next detector runs, so the digits of an IBAN are not also reported as a phone
number or a card.

Not found by patterns: people's names (that needs a name recognizer, such as
Presidio with a spaCy model, or the model option), addresses and dates of birth.
Presidio is a later option of T5 (card 4.9) and of Q4's redaction.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from ekb.contracts import PiiKind


@dataclass(frozen=True)
class Found:
    kind: PiiKind
    start: int
    end: int


def iban_valid(candidate: str) -> bool:
    """ISO 13616: move the first four characters to the end, letters to numbers, mod 97 = 1."""
    code = candidate.replace(" ", "").upper()
    if not 15 <= len(code) <= 34:
        return False
    digits = "".join(str(int(char, 36)) for char in code[4:] + code[:4])
    return int(digits) % 97 == 1


def luhn_valid(candidate: str) -> bool:
    """The check digit of payment card numbers."""
    digits = [int(char) for char in candidate if char.isdigit()]
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    for position, digit in enumerate(reversed(digits)):
        if position % 2:
            digit *= 2
            digit -= 9 if digit > 9 else 0
        total += digit
    return total % 10 == 0


_DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"


def es_id_valid(candidate: str) -> bool:
    """Spanish DNI (8 digits + letter) or NIE (X/Y/Z + 7 digits + letter): letter = n mod 23."""
    code = candidate.replace("-", "").upper()
    number = code[:-1]
    if number[0] in "XYZ":
        number = str("XYZ".index(number[0])) + number[1:]
    return number.isdigit() and _DNI_LETTERS[int(number) % 23] == code[-1]


def _digits_between(low: int, high: int) -> Callable[[str], bool]:
    return lambda text: low <= sum(char.isdigit() for char in text) <= high


_PHONE_WORDS = r"tel|tel\.|phone|telephone|telefono|teléfono|cellulare|cell|mobile|móvil|movil"

_IBAN_START = re.compile(r"\b[A-Z]{2}\d{2}")
_IBAN = re.compile(r"[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")


def _ibans(text: str) -> Iterator[Found]:
    """A trailing currency or reference can look like another printed IBAN group."""
    end = 0
    for start in _IBAN_START.finditer(text):
        if start.start() < end:
            continue
        match = _IBAN.match(text, start.start())
        if match is None:
            continue
        candidate = match.group()
        while candidate:
            if iban_valid(candidate):
                end = start.start() + len(candidate)
                yield Found(PiiKind.IBAN, start.start(), end)
                break
            candidate = candidate.rpartition(" ")[0]


DETECTORS: tuple[tuple[PiiKind, re.Pattern[str], Callable[[str], bool]], ...] = (
    (PiiKind.PAYMENT_CARD, re.compile(r"\b\d(?:[ -]?\d){12,18}\b"), luhn_valid),
    (
        PiiKind.IT_FISCAL_CODE,
        re.compile(r"\b[A-Z]{6}\d{2}[ABCDEHLMPRST]\d{2}[A-Z]\d{3}[A-Z]\b"),
        lambda _: True,
    ),
    (PiiKind.ES_NATIONAL_ID, re.compile(r"\b(?:\d{8}|[XYZ]-?\d{7})-?[A-Z]\b"), es_id_valid),
    (
        PiiKind.EMAIL,
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
        lambda _: True,
    ),
    (
        PiiKind.PHONE,
        re.compile(
            r"(?<![\w+])(?:\+|00)[1-9][\d ().-]{6,}\d"
            rf"|\b(?:{_PHONE_WORDS})\s*:?\s*\+?\d[\d ().-]{{6,}}\d",
            re.IGNORECASE,
        ),
        _digits_between(8, 15),
    ),
)


def find(text: str) -> Iterator[Found]:
    """Every piece of personal data the patterns recognize, in detector order."""
    masked = text
    for item in _ibans(text):
        yield item
        masked = masked[: item.start] + " " * (item.end - item.start) + masked[item.end :]
    for kind, pattern, valid in DETECTORS:
        for match in pattern.finditer(masked):
            if valid(match.group(0)):
                yield Found(kind, match.start(), match.end())
                width = match.end() - match.start()
                masked = masked[: match.start()] + " " * width + masked[match.end() :]


def kinds(text: str) -> tuple[PiiKind, ...]:
    """The kinds found in ``text``, each once, in a fixed order."""
    found = {item.kind for item in find(text)}
    return tuple(kind for kind in PiiKind if kind in found)
