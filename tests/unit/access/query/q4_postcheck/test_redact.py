"""Q4's redactors (S14): what is removed from released text, and what stays."""

from __future__ import annotations

import pytest

from ekb.access.query.q4_postcheck.options.redact_none import NoRedactor
from ekb.access.query.q4_postcheck.options.redact_rules import RulesRedactor
from ekb.contracts import PiiKind, Redaction

IBAN = "IT60 X054 2811 1010 0000 0123 456"  # the standard Italian example IBAN


@pytest.mark.parametrize(
    ("text", "released", "kind"),
    [
        (f"Sent again to IBAN {IBAN} after.", "Sent again to IBAN [IBAN removed] after.", "iban"),
        ("Card 4111 1111 1111 1111 on file", "Card [card number removed] on file", "payment_card"),
        (
            "Codice fiscale RSSMRA85T10A562S.",
            "Codice fiscale [fiscal code removed].",
            "it_fiscal_code",
        ),
        ("DNI 12345678Z", "DNI [ID number removed]", "es_national_id"),
    ],
)
def test_rules_remove_what_identifies_a_person_or_their_money(
    text: str, released: str, kind: str
) -> None:
    redacted = RulesRedactor().redact(text)
    assert redacted.text == released
    assert redacted.redactions == (Redaction(kind=PiiKind(kind), count=1),)


def test_rules_keep_contacts_and_ordinary_policy_text() -> None:
    """An HR page giving the payroll team's address must stay usable; figures are not IDs."""
    text = "Write to payroll@example.com or call +39 02 1234 5678. E3 | 52,000 | 61,000."
    redacted = RulesRedactor().redact(text)
    assert redacted.text == text
    assert redacted.redactions == ()


def test_rules_count_each_kind_and_keep_everything_around_it() -> None:
    text = f"A {IBAN}, B ES9121000418450200051332, card 4111-1111-1111-1111, mail a@example.com"
    redacted = RulesRedactor().redact(text)
    assert redacted.text == (
        "A [IBAN removed], B [IBAN removed], card [card number removed], mail a@example.com"
    )
    assert redacted.redactions == (
        Redaction(kind=PiiKind.IBAN, count=2),
        Redaction(kind=PiiKind.PAYMENT_CARD, count=1),
    )
    assert "0123" not in redacted.text
    assert RulesRedactor().redactor_id == "rules@1"


def test_none_releases_the_text_as_indexed() -> None:
    redacted = NoRedactor().redact(f"IBAN {IBAN}")
    assert redacted.text == f"IBAN {IBAN}"
    assert redacted.redactions == ()
    assert NoRedactor().redactor_id == "none@1"
