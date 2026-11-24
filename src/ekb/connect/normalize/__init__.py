"""T1 · Normalize: each source's vocabulary → the one ``ChangeEvent``.

One module per source type, each a small, pure translation table:

============  ==========================================  =====================
module        source says                                 ``op``
============  ==========================================  =====================
``docs``      page.created / .updated / .deleted / acl    upsert, delete, acl_change
``hr_api``    a record in a listing; a record gone        upsert, delete
``hr_db``     CDC r / c / u / d                           upsert, delete
``identity``  user.created / .deactivated, member +/-     upsert (the user record)
============  ==========================================  =====================

The functions only build events. Fetching, cursors and resolving people to
identity provider IDs happen in the connector options, so these tables can be
read, and tested, on their own. Shared rules are in ``common``.
"""
