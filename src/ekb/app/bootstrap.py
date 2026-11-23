"""Discover options and build steps from configuration."""

from __future__ import annotations

import importlib
import pkgutil

import ekb
from ekb.platform import registry
from ekb.platform.config import Settings


def load_options() -> list[str]:
    """Import every module under an ``options`` package so it registers itself.

    Returns the imported module names (useful for ``ekb options`` and tests).
    """
    imported: list[str] = []
    for module in pkgutil.walk_packages(ekb.__path__, prefix="ekb."):
        if ".options." in module.name:
            importlib.import_module(module.name)
            imported.append(module.name)
    return imported


def build_step(settings: Settings, step: str) -> object:
    """Instantiate the configured option for ``step``."""
    choice = settings.steps[step]
    return registry.create(step, choice.option, **choice.params)
