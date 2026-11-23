@AGENTS.md

## Claude Code specifics
- Skills: `/plan-task <n>`, `/new-option <step> <name>`, `/cross-review` (single source in `.agents/skills/`, shared with Codex).
- Your role is the author: implement tasks with `/plan-task <n>` and stop when the checks pass. Do not commit or push; Codex reviews, fixes, commits and pushes with `$cross-review`.
