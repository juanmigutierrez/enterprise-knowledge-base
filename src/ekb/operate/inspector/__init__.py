"""8 · Inspector: a local, read-only window onto what the knowledge base holds (D19).

It shows data an ordinary user must never see (every document, every
permission), so it is local-only by construction: it refuses to start under any
profile but ``local``, binds to 127.0.0.1, and has no write endpoint. Agents
never use it; they go through the MCP access layer.
"""
