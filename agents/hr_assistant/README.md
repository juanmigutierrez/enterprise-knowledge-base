# HR assistant (S15)

A small agent that asks the Knowledge API's `ask` tool over MCP, as the person its token
was issued to, and prints the answer with its sources. It imports nothing from `ekb`:
like any agent, it knows the MCP tools and its token, nothing else.

```powershell
uv run ekb api                       # prints one test token per person
$env:EKB_TOKEN = "<alice's token>"   # bash: export EKB_TOKEN=...
uv run python -m agents.hr_assistant "How many days of annual leave do I get?"
```

`--url` points it at another endpoint (default `http://127.0.0.1:8080/mcp`). A refused
token or an API that is not running exits with code 2 and says which.
