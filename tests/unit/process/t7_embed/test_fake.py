import math

import pytest

from ekb.process.t7_embed.options.fake import FakeEmbedder


def test_same_text_same_vector() -> None:
    embedder = FakeEmbedder(dimensions=16)
    first, second, other = embedder.embed(["leave policy", "leave policy", "expenses"]).vectors
    assert first == second
    assert first != other


def test_vectors_are_unit_length() -> None:
    (vector,) = FakeEmbedder(dimensions=64).embed(["x"]).vectors
    assert len(vector) == 64
    assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)


def test_its_id_says_fake_and_names_the_input_recipe_and_size() -> None:
    embedder = FakeEmbedder(dimensions=32)
    result = embedder.embed(["x"])
    assert embedder.model_id == "fake@1(input@1, sha256, 32)"
    assert result.model_id == embedder.model_id
    assert "no meaning" in result.note


def test_rejects_invalid_dimensions() -> None:
    with pytest.raises(ValueError, match="positive"):
        FakeEmbedder(dimensions=0)
