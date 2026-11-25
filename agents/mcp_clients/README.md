# MCP clients for the Knowledge API (S15)

Start the API first. It prints one test token per person (alice, bob, carla, emma, olivia),
all for the agent `hr-assistant`, and keeps them in the git-ignored
`.cache/ekb/test-tokens.json`, so a client configured once keeps working:

```powershell
uv run ekb api                       # offline: fake vectors, the extractive writer
uv run --env-file .env ekb api --models   # the profile's models (free-tier keys, Ollama for T7)
```

The endpoint is `http://127.0.0.1:8080/mcp` (Streamable HTTP, bound to 127.0.0.1 only).
Every request needs `Authorization: Bearer <token>`; without one the API answers 401.
The token decides whom the client acts for: no tool takes a user.

| Tool | What it returns |
|---|---|
| `whoami` | Who the token acts for, and the groups that person reads with now |
| `search` | The cited passages the person may read now (Q1 to Q4), to write your own answer from |
| `ask` | The HR assistant's short answer, written only from those passages, with numbered citations |

## Claude Code

```powershell
claude mcp add --transport http ekb http://127.0.0.1:8080/mcp --header "Authorization: Bearer <alice's token>"
```

Then ask in Claude Code: *"Use the ekb tools: how many days of annual leave do I get?"*.
Claude Code calls `ask` or `search` as alice. To act for someone else, remove the server
(`claude mcp remove ekb`) and add it again with their token. There is deliberately no
`.mcp.json` at the repository root, so your development sessions in this repository do
not pick the server up.

## MCP Inspector

```powershell
npx @modelcontextprotocol/inspector
```

In the Inspector: transport **Streamable HTTP**, URL `http://127.0.0.1:8080/mcp`, and under
authentication a bearer token (or a custom header `Authorization: Bearer <token>`). Connect,
open **Tools**, and run `whoami`, then `ask` with a question. Try `search` as alice and as bob
with *"salary bands"*: bob sees the restricted page, alice does not.

## The HR assistant (`agents/hr_assistant`)

```powershell
$env:EKB_TOKEN = "<alice's token>"
uv run python -m agents.hr_assistant "How many days of annual leave do I get?"
```
