"""Opening the docs tool's stored page: the Markdown body and the metadata T3 keeps."""

from __future__ import annotations

import pytest

from ekb.process.t3_parse.originals import docs_page
from ekb.process.t3_parse.port import MARKDOWN, ParseError
from tests.unit.process.t3_parse.conftest import page_json


def test_the_body_is_the_file_and_the_page_gives_title_and_language() -> None:
    file = docs_page("hr_docs:HR-PL-01", "1.1", page_json("# Annual leave policy\n"))
    assert (file.document_key, file.version, file.title, file.language) == (
        "hr_docs:HR-PL-01",
        "1.1",
        "Annual leave policy",
        "en",
    )
    assert (file.media_type, file.content) == (MARKDOWN, b"# Annual leave policy\n")
    assert file.department == "people"  # the page's own label, for T5 (S8)


def test_the_department_passes_through_the_parsers_and_may_be_missing() -> None:
    from ekb.process.t3_parse.options.markdown import MarkdownParser
    from ekb.process.t3_parse.options.plain_text import PlainTextParser

    filed = docs_page("k", "1", page_json("# Expenses\n", department="finance"))
    unfiled = docs_page("k", "1", page_json("# Note\n", department=None))
    for parser in (MarkdownParser(), PlainTextParser()):
        assert parser.parse(filed).department == "finance"
        assert parser.parse(unfiled).department is None


def test_fields_it_does_not_need_are_ignored() -> None:
    """A source adding a field must not break parsing."""
    file = docs_page("k", "1", page_json("x", sharing_link="https://example", language=None))
    assert (file.content, file.language) == (b"x", None)


def test_non_ascii_text_survives_the_envelope() -> None:
    file = docs_page("k", "1", page_json("Compensación: 30 € al mes"))
    assert file.content.decode() == "Compensación: 30 € al mes"


@pytest.mark.parametrize(
    ("original", "problem"),
    [
        (b'{"title": "No body"}', "body"),
        (b"not json at all", "not a docs tool page"),
        (b'{"title": "x", "body": "\xff"}', "not a docs tool page"),  # not UTF-8
        (b"[]", "not a docs tool page"),
    ],
)
def test_an_original_that_is_not_a_page_is_an_error(original: bytes, problem: str) -> None:
    with pytest.raises(ParseError, match=problem) as error:
        docs_page("hr_docs:HR-PL-01", "2.1", original)
    assert "hr_docs:HR-PL-01 v2.1" in str(error.value)
