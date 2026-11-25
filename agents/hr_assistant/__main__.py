"""``python -m agents.hr_assistant "question"``: ask the HR assistant as the token's person."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from agents.hr_assistant.client import DEFAULT_URL, AssistantError, ask, render

TOKEN_ENV = "EKB_TOKEN"  # noqa: S105  # nosec B105: the variable name, not a token


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agents.hr_assistant",
        description="Ask the HR assistant through the Knowledge API's MCP tools.",
    )
    parser.add_argument("question", help="Your question, in your own words.")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"The MCP endpoint ({DEFAULT_URL}).")
    parser.add_argument(
        "--token",
        default=None,
        help=f"Your token from `ekb api` (default: ${TOKEN_ENV}; an environment variable keeps "
        "it out of your shell history).",
    )
    args = parser.parse_args(argv)
    token = args.token or os.environ.get(TOKEN_ENV, "")
    if not token:
        print(
            f"No token: pass --token or set {TOKEN_ENV} (`uv run ekb api` prints them).",
            file=sys.stderr,
        )
        return 2
    try:
        answer = ask(args.url, token, args.question)
    except AssistantError as exc:
        print(f"HR assistant: {exc}", file=sys.stderr)
        return 2
    print("\n".join(render(answer)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
