"""What the search store indexes of a chunk, and how text is cut into terms (S13).

**What is indexed.** The keyword leg searches the same text T7 embeds
(``ekb.process.t7_embed.input``): the context header, the section trail and
the passage, joined. The header and the trail carry the words a question uses
when the passage itself doesn't ("You may carry up to 5 unused days" never says
"annual leave"). The stores cannot import a processing step, so the recipe is
repeated here; a test checks the two agree.

**How text becomes terms.** Lower case, accents folded (``días`` → ``dias``,
``Gómez`` → ``gomez``), then every run of letters or digits is a term
(``Ley 10/2021`` → ``ley``, ``10``, ``2021``; ``HR-PL-07`` → ``hr``, ``pl``,
``07``). No stemming and no stop words: ``parental`` does not match
``parentale``, and that is the vector leg's job. OpenSearch's language analyzers
(stemming per language) come with S17 and are an option to measure (card 4.5).
"""

from __future__ import annotations

import re
import unicodedata

from ekb.contracts import Chunk

TRAIL_SEPARATOR = " > "
_TERM = re.compile(r"[^\W_]+")


def indexed_text(chunk: Chunk) -> str:
    """The text the keyword leg searches for ``chunk``: header, trail, passage."""
    parts = (chunk.context_header, TRAIL_SEPARATOR.join(chunk.section), chunk.text)
    return "\n\n".join(part.strip() for part in parts if part.strip())


def fold(text: str) -> str:
    """Lower case with accents removed, so ``Días`` and ``dias`` are one term."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def terms(text: str) -> list[str]:
    """Every term of ``text``, in order, repeats included."""
    return _TERM.findall(fold(text))
