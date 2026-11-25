# 7 · Agents and MCP clients

Consumers of the access layer. Like `sources/`, this is outside the product:
agents only reach knowledge through the MCP tools of the Knowledge API
(`uv run ekb api`, S15), and `ekb` never imports this folder (an import-linter
contract).

| Folder | Purpose |
|---|---|
| `hr_assistant/` ✅ S15 | The POC use case: a small agent that asks the `ask` tool as the person its token was issued to, and prints the answer with its sources |
| `mcp_clients/` ✅ S15 | How to connect Claude Code and MCP Inspector to `http://127.0.0.1:8080/mcp` with a test token |
