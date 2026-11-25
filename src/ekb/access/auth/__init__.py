"""Auth for the Knowledge API (S15): the bearer token decides who a call acts for.

- ``port``: :class:`~ekb.access.auth.port.TokenCheck`, a token in, a
  :class:`~ekb.contracts.Caller` (one person, one agent) or ``None`` out.
- ``options/``: ``static_tokens`` (S15): random test tokens for a few test
  users, kept in a git-ignored file. An OIDC option (Keycloak locally,
  AgentCore Identity's on-behalf-of exchange in production, D9) comes with S17.

Default deny: no token, or a token the check does not know, gets no caller, and
the MCP layer answers 401 before any tool runs. Cedar policies on the caller's
agent (``acl_and_cedar``) are S15b.
"""
