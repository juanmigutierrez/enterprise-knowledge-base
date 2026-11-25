"""Q2 · Pre-filter: who is asking, as a search filters them (S13). Card 4.13.

- ``port``: :class:`~ekb.access.query.q2_prefilter.port.PreFilter`, what every
  option implements: a user ID in, a :class:`~ekb.contracts.Scope` out.
- ``options/``: ``acl`` (S13): the person's user ID and the groups they are in
  *now*, from the permission store; an unknown or inactive person reads nothing.
  ``acl_and_cedar`` (S15) will add Cedar policies (D8: "the roadmap agent never
  reads HR data", sensitivity rules) once an agent identity exists.

The scope is applied **inside** the search store, before anything is ranked
(:meth:`ekb.stores.search.port.SearchStore.keyword`): a chunk the person may not
read is never scored. Q4 (S14) checks again against the source before any text
is released.
"""
