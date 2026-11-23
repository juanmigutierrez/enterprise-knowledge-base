@AGENTS.md

## Claude Code specifics
- Skills: `/plan-task <n>`, `/new-option <step> <name>`, `/cross-review` (single source in `.agents/skills/`, shared with Codex).
- Use the `reviewer` subagent to cross-review code that Codex wrote. Code you write is reviewed by Codex (`$cross-review` or `/review` there).
