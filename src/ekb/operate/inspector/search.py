"""What the inspector shows of Q2 + Q3 (S13): one question, asked as one person.

The **Search as user** screen asks the same question as two people side by side.
Each side runs the real query path, the one the Knowledge API will run (S15):
Q2's scope from the permission store, then Q3's two legs over the chunks that
scope admits. The inspector does not wire it (``ekb.operate`` cannot import the
access layer); ``ekb.app`` passes it in as :attr:`SearchPanel.ask`.

**Withheld, for this screen only.** Next to the hits, the inspector counts the
chunks that share a word with the question but that this person may not read:
what the pre-filter kept out. Q3 cannot report this (it never sees those chunks),
and an agent must never learn it: it says a restricted page exists and roughly
what it is about. The inspector shows every document to whoever runs it
locally anyway (D19), so here it helps explain why two people get different
answers.

Deleted content is never shown: the search store hides a document as soon as
its delete is accepted (S11), for the hits and the withheld count alike.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ekb.contracts import Chunk, Principals, SearchResult, admits
from ekb.stores.search.port import SearchStore
from ekb.stores.search.text import indexed_text, terms

Ask = Callable[[str, str], SearchResult]
"""``ask(user_id, question)``: Q2 then Q3, as ``ekb.app.search`` wires them."""


@dataclass(frozen=True)
class SearchPanel:
    ask: Ask
    index: SearchStore
    """The search store the query path reads, for the withheld count."""
    encoder: str = ""
    """How questions are embedded, in one line (``ekb.app.search.encoder_note``)."""


def withheld(index: SearchStore, principals: Principals | None, question: str) -> tuple[Chunk, ...]:
    """Published chunks sharing a word with ``question`` that ``principals`` may not read.

    For the inspector and the demo only: it reads every chunk, which nothing on the
    query path may do.
    """
    wanted = set(terms(question))
    if not wanted:
        return ()
    return tuple(
        chunk
        for chunk in index.chunks()
        if chunk.acl is not None
        and not admits(chunk.acl, principals)
        and not wanted.isdisjoint(terms(indexed_text(chunk)))
    )
