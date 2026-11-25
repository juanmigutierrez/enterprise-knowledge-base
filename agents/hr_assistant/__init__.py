"""The HR assistant (S15): a small agent that asks the Knowledge API over MCP, as one person.

Run ``uv run ekb api`` first, then::

    EKB_TOKEN=<token> uv run python -m agents.hr_assistant "How many days of annual leave do I get?"

It imports nothing from ``ekb``: like any agent, it knows the MCP tools and the
token it was given, nothing else (an import-linter contract keeps ``ekb`` from
importing it back).
"""
