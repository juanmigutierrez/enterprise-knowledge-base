"""Counting tokens without a model: an approximation, on purpose.

A chunk's size is set in tokens because models count in tokens: the
embedding model's input limit, the context window, the price. But every model
has its own tokenizer (bge-m3's is not Cohere's, and neither is OpenAI's),
and loading one means downloading it. T4 needs a count that is the same
everywhere, offline, and good enough to keep chunks a sensible size.

So a token here is **a word or a punctuation mark**: ``\\w+`` or one
character that is neither a letter, a digit nor a space. ``32,000 EUR`` is 4
tokens (``32`` ``,`` ``000`` ``EUR``). Accented letters are letters, so Italian
and Spanish count like English. A real tokenizer splits long or rare words
into several pieces, so it counts a little more than this; the limits in
``config/*.yaml`` are therefore approximate. When the embedding model is
chosen (S10), its tokenizer can replace this counter. That changes where
chunks are cut, so it would be a new version of each chunker (``@2`` in its
ID), and the chunks it cut are visibly different from the ones before.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"\w+|[^\w\s]")


def count_tokens(text: str) -> int:
    """How many words and punctuation marks ``text`` has."""
    return sum(1 for _ in _TOKEN.finditer(text))


def token_spans(text: str) -> list[tuple[int, int]]:
    """Where each token starts and ends in ``text``: lets a window be cut out of the text
    itself, keeping its line breaks, instead of re-joining words with spaces."""
    return [match.span() for match in _TOKEN.finditer(text)]
