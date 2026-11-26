"""What every search store option checks and orders the same way (S11, S13; shared since S17).

- :func:`check_batch` is the publication gate at the store's door: all or
  nothing, before anything is written.
- :func:`best` keeps a leg's ``k`` best hits, ties broken by document then
  position, so results are stable whichever engine scored them.
"""

from __future__ import annotations

from ekb.contracts import Chunk
from ekb.stores.search.port import Scored, UnpublishableError


def best(scored: list[Scored], k: int) -> tuple[Scored, ...]:
    """The ``k`` best, ties broken by document then position, so results are stable."""
    ordered = sorted(scored, key=lambda s: (-s.score, s.chunk.document_key, s.chunk.position))
    return tuple(ordered[:k])


def check_batch(document_key: str, batch: tuple[Chunk, ...]) -> None:
    """The publication gate, checked again at the door: all or nothing."""
    for chunk in batch:
        if chunk.document_key != document_key:
            raise UnpublishableError(f"{chunk.chunk_id} belongs to {chunk.document_key}")
        if not chunk.publishable:
            missing = [
                name
                for name, value in (
                    ("readers (T8)", chunk.acl),
                    ("a sensitivity (T5)", chunk.sensitivity),
                    ("a vector (T7)", chunk.vector),
                )
                if value is None
            ]
            raise UnpublishableError(
                f"{chunk.chunk_id} is not publishable: missing {' and '.join(missing)}"
            )
    if len({chunk.original_lifetime for chunk in batch}) > 1:
        raise UnpublishableError(f"{document_key}: chunks belong to different lifetimes")
    versions = {chunk.document_version for chunk in batch}
    if len(versions) > 1:
        raise UnpublishableError(
            f"{document_key}: one version at a time, not {', '.join(sorted(versions))}"
        )
    ids = [chunk.chunk_id for chunk in batch]
    if len(set(ids)) != len(ids):
        raise UnpublishableError(f"{document_key}: the same chunk ID twice")
