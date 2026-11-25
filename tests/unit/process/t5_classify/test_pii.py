"""Personal data found by patterns (T5's rules, S8): what is found, and what is not."""

from __future__ import annotations

import pytest

from ekb.contracts import PiiKind
from ekb.process.t5_classify import pii


@pytest.mark.parametrize(
    ("text", "kinds"),
    [
        ("Pay to IBAN IT60 X054 2811 1010 0000 0123 456.", (PiiKind.IBAN,)),
        ("IBAN: ES9121000418450200051332", (PiiKind.IBAN,)),
        ("Card 4111 1111 1111 1111 on file", (PiiKind.PAYMENT_CARD,)),
        ("Card 4111-1111-1111-1111", (PiiKind.PAYMENT_CARD,)),
        ("Codice fiscale RSSMRA85T10A562S", (PiiKind.IT_FISCAL_CODE,)),
        ("DNI 12345678Z", (PiiKind.ES_NATIONAL_ID,)),
        ("NIE X-1234567-L", (PiiKind.ES_NATIONAL_ID,)),
        ("Write to payroll@example.com", (PiiKind.EMAIL,)),
        ("Call +39 02 1234 5678 today", (PiiKind.PHONE,)),
        ("Tel. 02 1234 5678", (PiiKind.PHONE,)),
        ("Llama al 0034 91 123 45 67", (PiiKind.PHONE,)),
        (
            "Mail a.b@example.com, IBAN IT60 X054 2811 1010 0000 0123 456",
            (PiiKind.IBAN, PiiKind.EMAIL),  # in PiiKind's order, each once
        ),
    ],
)
def test_patterns_find_personal_data(text: str, kinds: tuple[PiiKind, ...]) -> None:
    assert pii.kinds(text) == kinds


@pytest.mark.parametrize(
    "text",
    [
        "Full-time employees get 25 working days of paid annual leave.",
        "E3 | 52,000 | 61,000 | 70,000",  # salary figures are not identifiers
        "A home-office allowance of €25 per month, from 2026-04-01.",
        "Order 1234567890123 shipped.",  # 13 digits, but no valid card check digit
        "IBAN IT61 X054 2811 1010 0000 0123 456",  # one check digit off
        "DNI 12345678A",  # wrong control letter
        "Policy HR-PL-07 replaces HR-PL-99.",
        "Call extension 4512.",
    ],
)
def test_ordinary_policy_text_holds_no_personal_data(text: str) -> None:
    assert pii.kinds(text) == ()


def test_an_iban_s_digits_are_not_also_a_card_or_a_phone() -> None:
    found = list(pii.find("IBAN IT60 X054 2811 1010 0000 0123 456"))
    assert [f.kind for f in found] == [PiiKind.IBAN]


def test_check_digits() -> None:
    assert pii.iban_valid("GB82 WEST 1234 5698 7654 32")
    assert not pii.iban_valid("GB82 WEST 1234 5698 7654 33")
    assert not pii.iban_valid("IT60")  # too short to be an IBAN
    assert pii.luhn_valid("4111 1111 1111 1111")
    assert not pii.luhn_valid("0000 0000 0000 0000")  # one digit repeated is no card
    assert pii.es_id_valid("Y1234567X")
    assert not pii.es_id_valid("Y1234567A")


def test_currency_after_an_iban_is_outside_its_mask() -> None:
    account = "ES9121000418450200051332"
    text = f"{account} EUR {account} EUR"
    assert [(item.kind, text[item.start : item.end]) for item in pii.find(text)] == [
        (PiiKind.IBAN, account),
        (PiiKind.IBAN, account),
    ]


def test_high_risk_kinds_are_those_that_identify_a_person_or_their_money() -> None:
    assert {kind for kind in PiiKind if kind.high_risk} == {
        PiiKind.IBAN,
        PiiKind.PAYMENT_CARD,
        PiiKind.IT_FISCAL_CODE,
        PiiKind.ES_NATIONAL_ID,
    }
