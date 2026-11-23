"""Load the synthetic HR pages from Markdown files with YAML front matter.

Each file looks like::

    ---
    id: HR-PL-01
    title: Annual leave
    language: en
    department: people
    acl: { groups: [all-staff] }
    ---
    # Annual leave
    ...

Front matter keeps the metadata next to the text it describes, so a reviewer
can check a document and who may read it in one place.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from sources.common.seed import SeedError, split_front_matter
from sources.mock_docs.models import PageCreate

__all__ = ["SeedError", "load_pages", "parse_page"]


def parse_page(text: str, *, name: str = "<text>") -> PageCreate:
    """Parse one seed file into a page-create request."""
    meta, body = split_front_matter(text, name=name)
    try:
        return PageCreate(body=body, **meta)
    except ValidationError as exc:
        raise SeedError(f"{name}: {exc}") from exc


def load_pages(folder: Path) -> list[PageCreate]:
    """Every ``*.md`` file in ``folder``, sorted by file name for a stable order."""
    files = sorted(folder.glob("*.md"))
    if not files:
        raise SeedError(f"no seed documents in {folder}")
    return [parse_page(f.read_text(encoding="utf-8"), name=f.name) for f in files]
