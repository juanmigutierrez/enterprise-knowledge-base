"""What T7 embeds of a chunk: its context header, its section trail, then its text.

The **text** alone often lacks the words a question uses: "You may carry up to
5 unused days" never says "annual leave". The **context header** (T4, S8) puts
the passage back in its document, in the document's language. The **section
trail** names the document and the headings above the passage, which the
header may not repeat (and which exist when the header option is ``none``).
They are joined with blank lines, empty parts left out::

    From the Annual leave policy: carrying unused days over.   <- context header

    Annual leave policy > Carrying leave over                  <- section trail

    You may carry up to 5 unused days into the next year ...   <- text

The nearest heading may appear twice (in the trail and at the top of the text,
where T4 renders it); that does no harm. The header is embedded but never
quoted in an answer (S8).

``INPUT_VERSION`` names this recipe. It is in every vector's ID and in every
cache key (the gateway's ``input_version``): change the recipe, bump the
version, and every chunk's vector is visibly out of date, and none is reused.
"""

from __future__ import annotations

from ekb.contracts import Chunk

INPUT_VERSION = "input@1"
TRAIL_SEPARATOR = " > "


def embed_input(chunk: Chunk) -> str:
    """The text T7 sends to the embedding model for ``chunk``."""
    parts = (chunk.context_header, TRAIL_SEPARATOR.join(chunk.section), chunk.text)
    return "\n\n".join(part.strip() for part in parts if part.strip())
