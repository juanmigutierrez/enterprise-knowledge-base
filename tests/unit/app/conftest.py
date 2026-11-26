"""Shared fixtures for the composition root's tests.

``world`` (the four mocks, the change log and the worker, offline) is defined with the
worker's own tests and shared here, so the re-link tests (S16) run on the same world.
"""

from tests.unit.app.test_pipeline import world  # noqa: F401  (registers the fixture)
