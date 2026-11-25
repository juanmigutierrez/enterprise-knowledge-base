"""T8 · Permissions: the source's readers onto every chunk, then the publication gate.

Built in S11. Nothing is searchable before this step: a chunk reaches the
search store only with its readers (T8), its sensitivity (T5) and its vector
(T7). T2's fast lane comes straight here when only the readers changed, and
the identity provider's user records keep the permission store's memberships
current, so a group change needs no chunk rewritten.
"""
