"""Q4 · Post-check: re-check access against the source, redact, cite (S14). Card 4.13.

- ``port``: :class:`~ekb.access.query.q4_postcheck.port.PostCheck` (Q3's hits
  and Q2's scope in, a :class:`~ekb.contracts.Released` out),
  :class:`~ekb.access.query.q4_postcheck.port.SourceAccessCheck` (the source's
  current answer for one person and some documents) and
  :class:`~ekb.access.query.q4_postcheck.port.Redactor`.
- ``options/``: ``source_recheck`` (S14), and the redactors ``rules`` (the
  personal-data patterns T5 labels with, ``ekb.platform.pii``) and ``none``.
  Presidio is a later redactor.

The access check itself talks to the sources, so it lives with the connectors
(``ekb.connect.access_check``, the architecture's "current access check" arrow
from the Knowledge API to the connector) and is wired in ``ekb.app``.
"""
