"""5 · Permission store: who is in which group now, and each document's readers.

Built in S11: the ``PermissionStore`` port and an ``in_memory`` option. The
identity provider's user records keep the memberships current (through the
change log, like the entity store), and T8 records the readers it copied for
each document, with their source version. DynamoDB in production and S17.
"""
