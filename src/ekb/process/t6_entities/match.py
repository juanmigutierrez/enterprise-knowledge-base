"""The exact-match rules of T6: how a known name is found in a passage.

Precision comes first (card 4.10): a wrong link files a document under the
wrong person, while a missed one can still be found by the model tier later.
So the rules are narrow on purpose:

- **People by their full name or their work email**, ignoring case and
  accents: "Lucia Navarro" and "LUCÍA NAVARRO" both name Lucía Navarro. Never
  by a first name or a surname alone: "Marco" could be anyone.
- **Teams only next to a word that says "team"**: "People team", "Head of
  People", "equipo de People". "People", "Sales" and "Product" are also
  ordinary words, so a bare one is not a mention, and the name must be written
  as the HR system writes it, capital letter included ("people team" is not
  the People team).
- **Whole words only**: "Noah Martin" is not in "Noah Martinez" or in
  "Noah Martin-Leroy".
- **The longest match wins** where two names overlap, and **a name two
  entities share links to neither**: it is reported, not guessed.

Changing a rule means a new ``VERSION``, so chunks linked by the old rules can
be found by their ``linker_id`` and linked again.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from ekb.contracts import Entity, EntityKind, EntityMention

VERSION = 2

NAME, EMAIL, TEAM = "name", "email", "team"
"""The rules, as :attr:`EntityMention.rule` names them."""

TEAM_CUES = (
    r"{name}\s+(?i:team)",  # the People team
    r"(?i:team)\s+{name}",  # il team People
    r"(?i:head\s+of)\s+{name}",  # the Head of People
    r"(?i:equipo\s+de|departamento\s+de)\s+{name}",  # el equipo de People
    r"(?i:ufficio|squadra)\s+{name}",  # l'ufficio People
)
"""Where a team's name counts as a mention: next to one of these words (EN, ES, IT)."""

_BEFORE, _AFTER = r"(?<![\w-])", r"(?![\w-])"
"""Whole words: no letter, digit or hyphen right before or after the name."""
_EMAIL_BEFORE, _EMAIL_AFTER = r"(?<![\w.+-])", r"(?![\w@-]|\.\w)"
"""An email ends at a space or at a sentence's full stop, not inside a longer address."""


# ------------------------------------------------------------------ folding


@dataclass(frozen=True)
class Folded:
    """A text folded for matching, and where each of its characters came from."""

    text: str
    origin: tuple[int, ...]
    """For each character of ``text``, its index in the original text."""

    def span(self, start: int, end: int) -> tuple[int, int]:
        """The original text's span for ``text[start:end]``."""
        return self.origin[start], self.origin[end - 1] + 1


def fold(text: str) -> Folded:
    """Lower case, accents dropped, each run of white space made one space.

    Done character by character, keeping where each one came from, so a match
    in the folded text can be shown as the original words ("Lucia Navarro").
    """
    out: list[str] = []
    origin: list[int] = []
    in_space = False
    for index, char in enumerate(text):
        if char.isspace():
            if not in_space:
                out.append(" ")
                origin.append(index)
            in_space = True
            continue
        folded = "".join(
            c for c in unicodedata.normalize("NFKD", char) if not unicodedata.combining(c)
        ).casefold()
        if not folded:  # a lone accent mark: nothing to keep
            continue
        in_space = False
        out.extend(folded)
        origin.extend([index] * len(folded))
    return Folded("".join(out), tuple(origin))


def folded(name: str) -> str:
    return fold(name).text.strip()


def _spaced(literal: str) -> str:
    """``literal`` as a pattern, with any white space between its words allowed."""
    return r"\s+".join(re.escape(part) for part in literal.split())


# -------------------------------------------------------------------- index


@dataclass(frozen=True)
class _Rule:
    rule: str
    alias: str
    """The form as the entity store writes it, for reports."""
    owners: tuple[str, ...]
    """The entity IDs this form names: one, or more when it is ambiguous."""
    pattern: re.Pattern[str]
    on_folded: bool
    """Whether the pattern runs on the folded text (people) or the original (teams)."""


@dataclass(frozen=True)
class Found:
    """What one passage names."""

    mentions: tuple[EntityMention, ...]
    """One per entity, in the order the passage first names it."""
    ambiguous: tuple[str, ...] = ()
    """Names found that belong to more than one entity, so were not linked."""


class Index:
    """The match rules for a set of entities, built once per document."""

    def __init__(self, entities: Iterable[Entity]) -> None:
        owners: dict[tuple[str, str], set[str]] = defaultdict(set)
        shown: dict[tuple[str, str], str] = {}
        people = teams = 0
        for entity in entities:
            if entity.kind is EntityKind.PERSON:
                people += 1
                forms = [(NAME, entity.name)]
                forms += [(EMAIL if "@" in alias else NAME, alias) for alias in entity.aliases]
                for rule, alias in forms:
                    key = (rule, folded(alias))
                    if rule == NAME and " " not in key[1]:
                        continue
                    if key[1]:
                        owners[key].add(entity.entity_id)
                        shown.setdefault(key, alias.strip())
            elif entity.kind is EntityKind.TEAM:
                teams += 1
                for alias in (entity.name, *entity.aliases):
                    key = (TEAM, " ".join(alias.split()))
                    if key[1]:
                        owners[key].add(entity.entity_id)
                        shown.setdefault(key, key[1])
        self.people, self.teams = people, teams
        self._rules = tuple(
            _rule(rule, shown[(rule, key)], tuple(sorted(ids)), key)
            for (rule, key), ids in sorted(owners.items())
        )

    @property
    def ambiguous(self) -> tuple[str, ...]:
        """Every known form that names more than one entity."""
        return tuple(_ambiguity(r) for r in self._rules if len(r.owners) > 1)

    def find(self, text: str) -> Found:
        """The entities ``text`` names, by the rules above."""
        base = fold(text)
        spans: list[tuple[int, int, _Rule]] = []
        for rule in self._rules:
            haystack = base.text if rule.on_folded else text
            for match in rule.pattern.finditer(haystack):
                # Every span is kept in the original text's positions, to compare and show.
                start, end = base.span(*match.span()) if rule.on_folded else match.span()
                spans.append((start, end, rule))
        spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))  # leftmost first, then longest

        mentions: dict[str, EntityMention] = {}
        ambiguous: dict[str, None] = {}
        taken_until = 0
        for start, end, rule in spans:
            if start < taken_until:
                continue  # inside a longer name already matched
            taken_until = end
            if len(rule.owners) > 1:
                ambiguous[_ambiguity(rule)] = None
                continue
            (owner,) = rule.owners
            mentions.setdefault(
                owner, EntityMention(entity_id=owner, text=text[start:end], rule=rule.rule)
            )
        return Found(tuple(mentions.values()), tuple(ambiguous))


def _rule(rule: str, alias: str, owners: tuple[str, ...], key: str) -> _Rule:
    if rule == TEAM:
        name = _spaced(key)
        cues = "|".join(cue.format(name=name) for cue in TEAM_CUES)
        return _Rule(rule, alias, owners, re.compile(f"{_BEFORE}(?:{cues}){_AFTER}"), False)
    before, after = (_EMAIL_BEFORE, _EMAIL_AFTER) if rule == EMAIL else (_BEFORE, _AFTER)
    pattern = re.compile(f"{before}{_spaced(key)}{after}")
    return _Rule(rule, alias, owners, pattern, True)


def _ambiguity(rule: _Rule) -> str:
    return f"'{rule.alias}' names {len(rule.owners)} entities ({', '.join(rule.owners)})"
