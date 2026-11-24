"""T4 · Chunk and add a context header (layer 4, process). Cards 4.2 and 4.3.

T4 cuts a parsed document (T3) into chunks: the passages that are labelled
(T5), linked to entities (T6), embedded (T7), given permissions (T8) and
finally searched. Where the cuts fall decides what a search can find: a chunk
that mixes two topics matches both badly, and a table cut in half loses the
column names its numbers depend on.

Built in S6: the ``Chunker`` port and two options, ``structure_aware`` (cuts
along sections) and ``fixed`` (windows of tokens, the baseline). The context
header, a line that tells each chunk which document and section it is from,
comes in S8 through the model gateway (S7); until then it is empty.
"""
