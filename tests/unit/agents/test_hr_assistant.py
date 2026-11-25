"""``agents/hr_assistant`` (S15): asks over MCP with its token, prints the answer and sources.

It is outside the product: these tests talk to it the way a person would (its
command line) and to it through a real MCP server, never through ``ekb``'s code.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from agents.hr_assistant.__main__ import main
from agents.hr_assistant.client import AssistantError, ask, label, render

from ekb.access.auth.options.static_tokens import IssuedToken, StaticTokens, new_token
from ekb.access.mcp.server import create_app, create_server
from ekb.app.demos._serve import serving
from ekb.contracts import Answer, AssistantAnswer, Caller, Citation, Identity

ALICE = IssuedToken("alice", "hr-assistant", new_token())
CITATION = Citation(
    number=1,
    document_key="hr_docs:HR-PL-01",
    title="Annual leave policy",
    section=("Annual leave policy", "How much leave you get"),
    chunk_id="hr_docs:HR-PL-01#1",
    document_version="1.0",
)


class Service:
    def whoami(self, caller: Caller) -> Identity:
        return Identity(user_id=caller.user_id, agent_id=caller.agent_id, reads_as="x")

    def search(self, caller: Caller, question: str) -> Answer:
        return Answer(question=question, user_id=caller.user_id, reason="x")

    def ask(self, caller: Caller, question: str) -> AssistantAnswer:
        return AssistantAnswer(
            question=question,
            user_id=caller.user_id,
            agent_id=caller.agent_id,
            answer="You get 25 days [1].",
            citations=(CITATION,),
            notes=("keyword leg only",),
        )


@pytest.fixture
def url() -> Iterator[str]:
    server = create_server(Service(), StaticTokens(issued=[ALICE]), base_url="http://127.0.0.1")
    with serving(create_app(server)) as served:
        yield served.url + "/mcp"


def test_it_asks_as_its_token_and_prints_the_answer_with_sources(
    url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--url", url, "--token", ALICE.token, "How much leave?"]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed == [
        "HR assistant (hr-assistant for alice):",
        "  You get 25 days [1].",
        "Sources:",
        "  [1] HR-PL-01 · Annual leave policy > How much leave you get",
        "Note: keyword leg only",
    ]


def test_a_refused_token_or_no_api_is_said_plainly(
    url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(AssistantError, match="refused the token"):
        ask(url, "ekb_test_wrong", "q")
    with pytest.raises(AssistantError, match="could not reach the Knowledge API"):
        ask("http://127.0.0.1:9/mcp", ALICE.token, "q")
    monkeypatch.delenv("EKB_TOKEN", raising=False)
    assert main(["--url", url, "q"]) == 2
    assert "No token" in capsys.readouterr().err
    monkeypatch.setenv("EKB_TOKEN", "ekb_test_wrong")
    assert main(["--url", url, "q"]) == 2
    assert "refused the token (401)" in capsys.readouterr().err


def test_a_label_skips_a_heading_that_repeats_the_title_and_marks_a_changed_source() -> None:
    cited = CITATION.model_dump(mode="json")
    assert label(cited) == CITATION.label
    assert label({**cited, "source_changed": True}).endswith("(the source changed since indexing)")
    lines = render({"user_id": "bob", "agent_id": "a", "answer": "x", "cached": True})
    assert lines == ["HR assistant (a for bob, cached):", "  x"]
