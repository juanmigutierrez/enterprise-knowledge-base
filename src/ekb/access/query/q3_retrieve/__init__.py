"""Q3 · Retrieve: hybrid search over what the asker may read, fused, then the top k (S13).

Cards 4.5 (retrieval mode), 4.6 (reranking), 4.7 (how many chunks).

- ``port``: :class:`~ekb.access.query.q3_retrieve.port.Retriever` (a question
  and Q2's scope in, a :class:`~ekb.contracts.Retrieval` out),
  :class:`~ekb.access.query.q3_retrieve.port.QueryEncoder` (the question as a
  vector, comparable with the chunks') and
  :class:`~ekb.access.query.q3_retrieve.port.Reranker`.
- ``fusion``: reciprocal rank fusion, the rule that merges the two legs.
- ``options/``: ``hybrid_rrf`` (keywords + vectors, fused) and the reranker
  ``none`` (S13). A real reranker (bge-reranker, Cohere Rerank) waits for card
  4.6; single-leg options (``keyword_only``, ``vector_only``) for card 4.5 (S18).
"""
