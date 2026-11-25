"""MCP server exposing the Knowledge API (S15, D9): ``whoami``, ``search`` and ``ask``.

- ``server``: :func:`~ekb.access.mcp.server.create_server` builds the MCP
  server around a :class:`~ekb.access.mcp.server.KnowledgeService` (the query
  path and the HR assistant, wired in ``ekb.app``) and a
  :class:`~ekb.access.auth.port.TokenCheck`;
  :func:`~ekb.access.mcp.server.create_app` serves it over Streamable HTTP.

Every call carries a bearer token, and the token alone says who the call acts
for: no tool takes a user ID. ``get_document``, ``get_entity``,
``query_metrics`` and ``changes_since`` (D9) come later.
"""
