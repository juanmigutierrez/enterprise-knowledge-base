"""5 · Search store: the published chunks, the only ones an agent can find.

Built in S11: the ``SearchStore`` port and an ``in_memory`` option. T8 publishes
to it only chunks that have readers, a sensitivity and a vector (the
publication gate), T2's fast lane changes their readers in place, and a purge
removes a deleted document. S13 adds ranking, filtered by readers first: BM25
over the words and cosine similarity over the vectors (``text`` says what is
indexed and how it is cut into terms). OpenSearch comes in S17. Card 4.16.
"""
