"""Option registry: how every step can swap implementations without code changes.

Each step (``t7_embed``, ``q3_retrieve``, ``bus`` ...) defines a port, a
``typing.Protocol``. Each option is a class in that step's ``options/`` folder,
registered under a name::

    @register("t7_embed", "model")
    class ModelEmbedder: ...

The composition root (``ekb.app``) reads the configured option name for each
step and calls :func:`create`. Experiments in ``evaluation/experiments`` do the
same with a list of options, which is how we compare them on equal terms.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Factory = Callable[..., object]

_FACTORIES: dict[str, dict[str, Factory]] = {}


class UnknownOptionError(LookupError):
    """Raised when config names an option that is not registered."""


def register[F: Factory](step: str, name: str) -> Callable[[F], F]:
    """Class or function decorator that registers an option for a step."""

    def decorator(factory: F) -> F:
        options = _FACTORIES.setdefault(step, {})
        if name in options and options[name] is not factory:
            raise ValueError(f"option {name!r} is already registered for step {step!r}")
        options[name] = factory
        return factory

    return decorator


def create(step: str, name: str, /, **params: Any) -> object:
    """Instantiate the option ``name`` for ``step`` with its config params."""
    try:
        factory = _FACTORIES[step][name]
    except KeyError:
        available = ", ".join(options(step)) or "none"
        raise UnknownOptionError(
            f"no option {name!r} for step {step!r} (available: {available})"
        ) from None
    return factory(**params)


def options(step: str) -> list[str]:
    """Names of the options registered for ``step``."""
    return sorted(_FACTORIES.get(step, {}))


def steps() -> list[str]:
    """Steps that have at least one registered option."""
    return sorted(_FACTORIES)
