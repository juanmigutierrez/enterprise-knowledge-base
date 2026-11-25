"""Agent contract: who is calling, and what the HR assistant answers (S15).

The Knowledge API (box 6) serves agents over MCP. Two things cross that
boundary, so they live here, next to S14's :mod:`ekb.contracts.answer`:

- :class:`Caller`: **who a call acts for**, as the token says: one person and
  one agent. It comes from the bearer token only, never from a tool argument,
  so an agent cannot choose whom it acts as (D9: "agents act as the signed-in
  user", "no super-user service account").
- :class:`Identity`, :class:`AssistantAnswer`: what the ``whoami`` and ``ask``
  tools return. ``search`` returns S14's :class:`~ekb.contracts.Answer`.

Like :class:`~ekb.contracts.Answer`, an :class:`AssistantAnswer` **never names
a document Q4 held back**: its text is written only from released passages,
and its citations are theirs.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ekb.contracts.answer import Citation


class Caller(BaseModel):
    """Who one MCP call acts for: the person and the agent the token was issued to."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    """The identity provider's user ID (D3): the person the agent acts for."""
    agent_id: str
    """The agent the token was issued to, e.g. ``hr-assistant``. Cedar's principal (S15b)."""
    token_id: str
    """A label for the token, safe to log, e.g. ``static:alice@hr-assistant``. Never the token."""

    @property
    def label(self) -> str:
        """``hr-assistant for alice``."""
        return f"{self.agent_id} for {self.user_id}"


class Identity(BaseModel):
    """``whoami``: who this connection acts for, and what they read with now."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    agent_id: str
    reads_as: str
    """Q2's answer in plain words, e.g. ``reads as alice + all-staff, it-employees``."""
    groups: tuple[str, ...] = ()
    """The groups the person reads with now, from the identity sync's copy (Q2)."""
    reads_nothing: bool = False
    """Unknown to the identity provider, or has left: every search comes back empty."""


class AssistantAnswer(BaseModel):
    """``ask``: the HR assistant's answer, written only from passages Q4 released."""

    model_config = ConfigDict(frozen=True)

    question: str
    user_id: str
    agent_id: str
    answer: str
    """The answer, with ``[n]`` markers that refer to :attr:`citations`."""
    citations: tuple[Citation, ...] = ()
    """The released passages the answer refers to, by number."""
    notes: tuple[str, ...] = ()
    """What the reader should know (a split question, passages that could not be checked,
    a cached answer reused after a fresh check). Never a held-back document."""
    cached: bool = False
    """Reused from the answer cache, after Q4 confirmed every cited page again just now."""
    options: tuple[str, ...] = ()
    """The Q1 to Q4 options and the answer writer that answered."""
