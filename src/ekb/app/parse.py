"""T3 wired to the change log's raw store (S5).

Two choices are made here, in the composition root, and nowhere else:

- **Which sources hold documents**, and how to open their originals:
  ``DOCUMENT_SOURCES``. Only the docs tool today. HR records, database rows and
  identity users are structured records: T3 reports them as "not a document"
  and they never become chunks (D7).
- **Which parser.** By name. ``config/default.yaml`` still names ``docling``,
  the starting choice of card 4.1 (it changes only with an experiment result);
  until it is built the worker pipeline (S12) runs its stand-in, ``markdown``
  (``ekb.app.choices``).

T3 runs only where T2 decided ``process``. The worker pipeline (S12) runs it
behind T2 for every event; :func:`parse_after_t2` is the step demos' hand
wiring of the same rule.
"""

from __future__ import annotations

from collections.abc import Callable

from ekb.app.changelog import ChangeLog
from ekb.contracts import DiffAction
from ekb.platform import registry
from ekb.process.t2_diff.step import DiffResult, Downstream
from ekb.process.t3_parse.options.markdown import MarkdownParser
from ekb.process.t3_parse.options.plain_text import PlainTextParser
from ekb.process.t3_parse.originals import OriginalReader, docs_page
from ekb.process.t3_parse.port import Parser
from ekb.process.t3_parse.step import ParseResult, ParseStep

DOCUMENT_SOURCES: dict[str, OriginalReader] = {"hr_docs": docs_page}
"""Sources whose items are documents, each with the reader that opens its originals."""

PARSERS = (MarkdownParser, PlainTextParser)  # imported, so both are registered
DEFAULT_PARSER = "markdown"


def parser(name: str = DEFAULT_PARSER) -> Parser:
    """A registered T3 option by name (``markdown``, ``plain_text``)."""
    built = registry.create("t3_parse", name)
    if not isinstance(built, Parser):
        raise TypeError(f"t3_parse option {name!r} does not implement the Parser port")
    return built


def parse_step(changelog: ChangeLog, name: str = DEFAULT_PARSER) -> ParseStep:
    """T3 over the change log's raw store."""
    return ParseStep(parser(name), changelog.rawstore, DOCUMENT_SOURCES)


def parse_after_t2(
    t3: ParseStep,
    keep: Callable[[ParseResult], object],
    forget: Callable[[str], object],
) -> Downstream:
    """T2's ``downstream`` in the step demos: parse what T2 sends on, forget what it purges.

    ``process`` → T3 parses the event's original and ``keep`` gets the result.
    ``purge`` → ``forget`` gets the document key: a deleted item's parsed text
    must go too, never only its original. ``skip`` and ``acl_only`` → nothing:
    the text didn't change, so parsing it again would give the same blocks.

    If T3 raises, T2's step records nothing and the event comes back (S4).
    """

    def downstream(result: DiffResult) -> None:
        decision = result.decision
        if decision.action is DiffAction.PROCESS:
            keep(t3.handle(decision.event))
        elif decision.action is DiffAction.PURGE:
            forget(decision.event.document_key)

    return downstream
