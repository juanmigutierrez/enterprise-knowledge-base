"""The behaviour suites' fixture (S17): each test once per place an option can run."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack

import pytest

from tests.contract.infra import WHERE, Infra
from tests.fakes.aws import moto_clients


@pytest.fixture(params=WHERE)
def infra(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[Infra]:
    where = str(request.param)
    with ExitStack() as stack:
        if where == "moto":
            dynamodb, s3 = stack.enter_context(moto_clients(monkeypatch))
            built = Infra(where, dynamodb, s3)
        else:
            built = Infra(where)
        try:
            yield built
        finally:
            for drop in built._drops:
                drop()
