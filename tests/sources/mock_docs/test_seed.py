"""The synthetic documents are what the later gates are tested against."""

from __future__ import annotations

from pathlib import Path

import pytest
from sources.common.directory import Directory, UnknownPrincipalError
from sources.mock_docs.seed import SeedError, load_pages, parse_page
from sources.mock_docs.store import DEFAULT_DATA_DIR, DocsStore

VALID = """---
id: HR-T-01
title: Test
language: en
department: people
acl: { groups: [all-staff] }
---
# Test
Body.
"""


def test_seed_has_twelve_documents_in_three_languages() -> None:
    pages = load_pages(DEFAULT_DATA_DIR / "hr_docs")
    assert len(pages) == 12
    by_language = {lang: sum(p.language == lang for p in pages) for lang in ("en", "it", "es")}
    assert by_language == {"en": 7, "it": 3, "es": 2}


def test_exactly_one_document_is_restricted_to_compensation() -> None:
    pages = load_pages(DEFAULT_DATA_DIR / "hr_docs")
    restricted = [
        p.id for p in pages if "all-staff" not in p.acl.groups and "hr-team" not in p.acl.groups
    ]
    assert restricted == ["HR-CMP-01"]
    salary = next(p for p in pages if p.id == "HR-CMP-01")
    assert salary.acl.groups == ("hr-compensation",)
    assert salary.acl.users == ()


def test_document_to_delete_later_is_seeded_and_superseded() -> None:
    pages = {p.id: p for p in load_pages(DEFAULT_DATA_DIR / "hr_docs")}
    assert "HR-PL-99" in pages
    assert "HR-PL-99" in pages["HR-PL-06"].body  # the replacement says what it replaces


def test_every_seed_permission_names_a_known_user_or_group() -> None:
    directory = Directory.load(DEFAULT_DATA_DIR / "directory.yaml")
    for page in load_pages(DEFAULT_DATA_DIR / "hr_docs"):
        directory.check(users=page.acl.users, groups=page.acl.groups)  # raises if unknown


def test_only_bob_can_read_the_salary_bands() -> None:
    directory = Directory.load(DEFAULT_DATA_DIR / "directory.yaml")
    readers = {uid for uid, user in directory.users.items() if "hr-compensation" in user.groups}
    assert readers == {"bob"}


def test_parse_page_reads_front_matter_and_body() -> None:
    page = parse_page(VALID)
    assert page.id == "HR-T-01"
    assert page.acl.groups == ("all-staff",)
    assert page.body == "# Test\nBody.\n"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("# no front matter\n", "must start"),
        ("---\nid: X\n# never closed\n", "not closed"),
        ("---\n- a list\n---\nbody\n", "mapping"),
        (VALID.replace("language: en", "language: english"), "language"),
        (VALID.replace("department: people\n", "department: people\nsecret: 1\n"), "secret"),
    ],
)
def test_malformed_seed_files_are_rejected(text: str, message: str) -> None:
    with pytest.raises(SeedError, match=message):
        parse_page(text, name="bad.md")


def test_empty_seed_folder_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(SeedError, match="no seed documents"):
        load_pages(tmp_path)


def test_seed_with_unknown_group_fails_to_load(tmp_path: Path) -> None:
    (tmp_path / "hr_docs").mkdir()
    (tmp_path / "hr_docs" / "a.md").write_text(VALID.replace("all-staff", "all-staf"))
    (tmp_path / "directory.yaml").write_bytes((DEFAULT_DATA_DIR / "directory.yaml").read_bytes())
    with pytest.raises(UnknownPrincipalError, match="all-staf"):
        DocsStore.from_seed(tmp_path)
