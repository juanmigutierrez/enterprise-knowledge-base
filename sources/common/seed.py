"""Read the synthetic data in ``sources/synthetic_data``.

Two formats: plain YAML for records and tables, and Markdown with YAML front
matter for documents (metadata next to the text it describes).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DATA_DIR = Path(__file__).resolve().parents[1] / "synthetic_data"

_FENCE = "---"


class SeedError(ValueError):
    """A seed file is malformed."""


def read_yaml(path: Path) -> dict[str, Any]:
    """A YAML file whose top level is a mapping."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SeedError(f"{path.name}: the top level must be a mapping")
    return data


def split_front_matter(text: str, *, name: str = "<text>") -> tuple[dict[str, Any], str]:
    """Split ``---``-fenced YAML front matter from the Markdown body."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        raise SeedError(f"{name}: must start with a '---' front matter fence")
    try:
        end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == _FENCE)
    except StopIteration:
        raise SeedError(f"{name}: front matter is not closed with '---'") from None
    meta = yaml.safe_load("\n".join(lines[1:end]))
    if not isinstance(meta, dict):
        raise SeedError(f"{name}: front matter must be a mapping")
    body = "\n".join(lines[end + 1 :]).strip() + "\n"
    return meta, body
