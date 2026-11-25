"""What T7 embeds of a chunk (S10): header, section trail, text; empty parts left out."""

from __future__ import annotations

from ekb.contracts import Chunk
from ekb.process.t7_embed.input import INPUT_VERSION, embed_input


def chunk(**fields: object) -> Chunk:
    base: dict[str, object] = {
        "document_key": "hr_docs:HR-PL-01",
        "document_version": "1",
        "chunk_id": "hr_docs:HR-PL-01#1",
        "text": "You may carry up to 5 unused days into the next year.",
    }
    return Chunk.model_validate({**base, **fields})


def test_header_then_trail_then_text_separated_by_blank_lines() -> None:
    text = embed_input(
        chunk(
            context_header="From the Annual leave policy: carrying days over.",
            section=("Annual leave policy", "Carrying leave over"),
        )
    )

    assert text == (
        "From the Annual leave policy: carrying days over.\n\n"
        "Annual leave policy > Carrying leave over\n\n"
        "You may carry up to 5 unused days into the next year."
    )


def test_a_missing_header_or_trail_leaves_no_empty_paragraph() -> None:
    assert embed_input(chunk()) == "You may carry up to 5 unused days into the next year."
    assert embed_input(chunk(section=("Annual leave policy",), context_header="  ")) == (
        "Annual leave policy\n\nYou may carry up to 5 unused days into the next year."
    )


def test_the_recipe_has_a_version() -> None:
    assert INPUT_VERSION == "input@1"
