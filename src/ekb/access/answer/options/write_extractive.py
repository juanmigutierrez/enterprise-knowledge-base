"""``answer: extractive``: the released passages' opening sentences, with citations, no model.

For the ``ci`` profile and the offline demos, and the stand-in the ``model``
writer falls back to when no model answers. It cannot reason, translate or
combine: it quotes. For "How many days of annual leave do I get?" it writes::

    From the HR documents you can read:
    - Full-time employees get 25 working days of paid leave per calendar year. [1]
    - Request leave in the HR portal at least two weeks before. [2]

Each quote is the first sentence of one passage, with the passage's own heading
(it repeats the section trail) and list markers taken off, cut at
:data:`MAX_WORDS`. At most :data:`MAX_QUOTES` passages, in Q4's order, which is
Q3's ranking. It only ever reads the released text, so it cannot quote more
than Q4 released.
"""

from ekb.access.answer.quoting import PassageQuoter
from ekb.platform.registry import register


@register("answer", "extractive")
class ExtractiveWriter(PassageQuoter):
    """Quote the first sentences of released passages."""
