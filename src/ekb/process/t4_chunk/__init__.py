"""T4 · Chunk and add a context header (layer 4, process). Cards 4.2 and 4.3.

T4 cuts a parsed document (T3) into chunks: the passages that are labelled
(T5), linked to entities (T6), embedded (T7), given permissions (T8) and
finally searched. Where the cuts fall decides what a search can find: a chunk
that mixes two topics matches both badly, and a table cut in half loses the
column names its numbers depend on.

Built in S6: the ``Chunker`` port and two options, ``structure_aware`` (cuts
along sections) and ``fixed`` (windows of tokens, the baseline).

Built in S8: the **context header**, one or two sentences that tell each chunk
which document and topic it is from, so a search finds a chunk that never names
its subject. It is a port of its own (``header.py``, ``HeaderWriter``, registered
as ``t4_header``) with two options: ``none`` (the baseline of card 4.3) and
``model`` (one call per document through the model gateway, S7).
"""
