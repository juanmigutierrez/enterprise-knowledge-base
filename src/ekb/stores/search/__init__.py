"""5 · Search store: the published chunks, the only ones an agent can find.

Built in S11: the ``SearchStore`` port and an ``in_memory`` option. T8 publishes
to it only chunks that have readers, a sensitivity and a vector (the
publication gate), T2's fast lane changes their readers in place, and a purge
removes a deleted document. It filters by readers now; ranking (BM25 + vectors
+ entity IDs) comes in S13, OpenSearch in S17. Card 4.16.
"""
