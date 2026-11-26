"""Publication gate (card 4.14's publication test): nothing is searchable before T8 has
set its readers and sensitivity, and once it has, only those readers find it.

Ingestion is paused after T7 (embedding) and before T8 publishes: the page is parsed,
chunked, labelled, linked and embedded, and still no one finds it.
"""

from __future__ import annotations

import pytest

from ekb.app.pipeline import EventFailedError
from tests.gates.story import Story, pages

pytestmark = pytest.mark.filterwarnings(
    "ignore:You should not use the 'timeout' argument with the TestClient"
)

KEY = "hr_docs:HR-PL-12"
QUESTION = "When is the HR offsite in Seville?"
PAGE = {
    "id": "HR-PL-12",
    "title": "HR offsite 2027",
    "language": "en",
    "department": "people",
    "acl": {"groups": ["hr-team"]},
    "body": "# HR offsite 2027\n\nThe HR offsite in Seville is on 12 March 2027.",
}


def test_a_page_is_invisible_until_t8_publishes_it_and_then_only_to_its_readers(
    story: Story, monkeypatch: pytest.MonkeyPatch
) -> None:
    t8 = story.system.pipeline.steps.t8
    embedded: list[str] = []
    publish = t8.publish

    def paused(event: object, document: object, chunks: object) -> object:
        embedded.extend(c.chunk_id for c in chunks)  # type: ignore[attr-defined]
        raise RuntimeError("ingestion paused before T8")

    monkeypatch.setattr(t8, "publish", paused)
    story.sources.docs.post("/pages", json=PAGE, headers={"X-Actor": "dana"}).raise_for_status()
    with pytest.raises(EventFailedError, match="T8 publish"):
        story.sync()

    assert embedded  # T3 to T7 ran: the chunks exist, with vectors
    assert story.chunks(KEY) == ()
    for user in ("bob", "lucia", "alice"):
        assert KEY not in pages(story.search(user, QUESTION))
        assert KEY not in pages(story.ask(user, QUESTION).reply)

    monkeypatch.setattr(t8, "publish", publish)
    story.sync()  # the event comes back and T8 publishes it

    assert all(chunk.publishable for chunk in story.chunks(KEY))
    assert KEY in pages(story.search("bob", QUESTION))
    assert KEY in pages(story.search("lucia", QUESTION))
    assert KEY not in pages(story.search("alice", QUESTION))
    assert not story.told("Seville is on", users=("alice",))
