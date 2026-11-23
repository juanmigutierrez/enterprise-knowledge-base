"""4 · Process: the worker pipeline T2 -> T8.

Each step is a sub-package with a ``port.py`` (Protocol) and an ``options/`` folder.
Steps never import each other (enforced by import-linter); the pipeline wires them.
"""
