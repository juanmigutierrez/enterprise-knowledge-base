# 7 · Agents and MCP clients

Consumers of the access layer. Like `sources/`, this is outside the product:
agents only reach knowledge through the MCP tools of the Knowledge API.

| Folder (planned) | Purpose |
|---|---|
| `hr_assistant/` | The POC use case: a small agent that answers HR questions with citations, acting as a given test user |
| `mcp_clients/` | Example client configs (Claude Desktop / Claude Code, OpenCode, MCP Inspector) pointing at `http://localhost:8080` |
