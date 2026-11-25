"""``auth: static_tokens`` (S15): random test tokens, kept out of git, compared by hash.

The gates: an unknown, empty or almost-right token is no caller (default deny);
the token file is reused, so a configured client keeps working, and a user
removed from the configuration loses their token.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess  # nosec B404: inspect the token file ACL
from pathlib import Path

from ekb.access.auth.options.static_tokens import (
    PREFIX,
    IssuedToken,
    StaticTokens,
    load_or_issue,
    new_token,
)
from ekb.contracts import Caller


def test_a_token_names_one_person_and_one_agent() -> None:
    caller_label = "static:" + "alice@hr-assistant"  # a label, safe to log: never the token
    alice = IssuedToken("alice", "hr-assistant", new_token())
    tokens = StaticTokens(issued=[alice, IssuedToken("bob", "hr-assistant", new_token())])
    caller = tokens.verify(alice.token)
    assert caller == Caller(user_id="alice", agent_id="hr-assistant", token_id=caller_label)
    assert tokens.auth_id == "static_tokens@1(hr-assistant, 2 users)"


def test_anything_but_an_issued_token_is_no_caller() -> None:
    """Default deny: no guest, no prefix match, no case folding, no trimming."""
    alice = IssuedToken("alice", "hr-assistant", new_token())
    tokens = StaticTokens(issued=[alice])
    for wrong in ("", "guess", PREFIX, alice.token[:-1], alice.token.upper(), alice.token + " "):
        assert tokens.verify(wrong) is None
    assert StaticTokens(issued=[]).verify(alice.token) is None


def test_tokens_are_random_prefixed_and_never_shown_by_repr() -> None:
    first, second = new_token(), new_token()
    assert first != second
    assert first.startswith(PREFIX)
    assert len(first) > len(PREFIX) + 40  # 32 random bytes
    issued = IssuedToken("alice", "hr-assistant", first)
    assert first not in repr(issued)


def test_the_token_file_is_written_once_and_reused(tmp_path: Path) -> None:
    path = tmp_path / "ekb" / "test-tokens.json"
    first = load_or_issue(path, ["alice", "bob"], "hr-assistant")
    assert path.exists()
    if os.name == "nt":
        # SDDL is independent of the language of the Windows installation.
        powershell = (
            Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        acl = subprocess.check_output(  # noqa: S603  # nosec B603: fixed local inspection
            [
                str(powershell),
                "-NoProfile",
                "-Command",
                "[IO.File]::GetAccessControl($env:TOKEN_PATH).GetSecurityDescriptorSddlForm('Access')",
            ],
            env={**os.environ, "TOKEN_PATH": str(path)},
            text=True,
        )
        dacl = acl.strip().split("D:", 1)[1]
        assert dacl in {"P(A;;FA;;;OW)", "PAI(A;;FA;;;OW)"}
    else:
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    again = load_or_issue(path, ["alice", "bob"], "hr-assistant")
    assert again == first
    tokens = StaticTokens(path, users=["alice", "bob"], agent="hr-assistant")
    caller = tokens.verify(first[0].token)
    assert caller is not None
    assert caller.user_id == "alice"


def test_a_new_user_gets_a_token_and_a_removed_one_loses_theirs(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    first = {t.user_id: t.token for t in load_or_issue(path, ["alice", "bob"], "hr-assistant")}
    later = {t.user_id: t.token for t in load_or_issue(path, ["alice", "carla"], "hr-assistant")}
    assert later["alice"] == first["alice"]
    assert "carla" in later
    tokens = StaticTokens(path, users=["alice", "carla"], agent="hr-assistant")
    assert tokens.verify(first["bob"]) is None
    assert set(json.loads(path.read_text())["tokens"]) == {"alice", "carla"}


def test_an_unreadable_file_or_another_agent_gets_new_tokens(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    path.write_text("not json", encoding="utf-8")
    issued = load_or_issue(path, ["alice"], "hr-assistant")
    assert issued[0].token.startswith(PREFIX)
    other = load_or_issue(path, ["alice"], "roadmap-agent")
    assert other[0].token != issued[0].token
    assert other[0].agent_id == "roadmap-agent"
    path.write_text(json.dumps({"agent": "hr-assistant", "tokens": {"alice": "plain"}}))
    assert load_or_issue(path, ["alice"], "hr-assistant")[0].token.startswith(PREFIX)


def test_duplicate_and_truncated_stored_tokens_are_rotated(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    duplicate = new_token()
    stored = {"alice": duplicate, "bob": duplicate, "carla": PREFIX}
    path.write_text(json.dumps({"agent": "hr-assistant", "tokens": stored}))
    tokens = StaticTokens(path, users=list(stored))
    assert tokens.verify(duplicate) is None
    assert tokens.verify(PREFIX) is None
    assert len({item.token for item in tokens.issued}) == len(stored)
