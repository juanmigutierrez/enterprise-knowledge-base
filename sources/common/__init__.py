"""Shared building blocks for the mock sources.

Each mock keeps only what makes it different (its data shapes and rules). The
plumbing they all need lives here:

- ``factory``: builds a mock's FastAPI app and runs it from the command line
- ``feed``: an append-only change feed with a resumable cursor
- ``seed``: reading synthetic data (YAML, Markdown with front matter)
- ``clock``: an injectable clock, so tests are deterministic
- ``directory``: the test users and groups that several mocks share
"""
