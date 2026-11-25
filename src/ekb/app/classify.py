"""T5 wired behind T4 (S8), and T2's purge extended to the model cache.

Two choices are made here, in the composition root, and nowhere else:

- **Which classifier.** ``rules`` or ``model_plus_rules``, by hand, as the
  earlier steps chose theirs; choosing from config comes with the pipeline
  (S12). ``config/default.yaml`` names ``model_plus_rules`` (card 4.9's
  starting choice) and the ``ci`` profile names ``rules``.
- **What a purge forgets.** A deleted document's parsed text and chunks, as in
  S5 and S6, and now its **cached model replies** too: a context header or a
  model's labels are derived from the document's text, so they must not outlive
  it. :func:`forgetter` builds T3's ``forget`` with ``gateway.forget`` in it.

T5 runs after T4 inside :func:`ekb.app.chunk.chunk_after_t3`.
"""

from __future__ import annotations

from collections.abc import Callable

from ekb.platform import registry
from ekb.platform.gateway.gateway import ModelGateway
from ekb.platform.gateway.port import Completer
from ekb.process.t5_classify.options.model_plus_rules import ModelPlusRulesClassifier
from ekb.process.t5_classify.options.rules import RulesClassifier
from ekb.process.t5_classify.port import Classifier
from ekb.process.t5_classify.step import ClassifyStep

CLASSIFIERS = (RulesClassifier, ModelPlusRulesClassifier)  # imported, so both are registered
DEFAULT_CLASSIFIER = "rules"
CLASSIFIER_OPTIONS = ("rules", "model_plus_rules")
MODEL_CLASSIFIERS = frozenset({"model_plus_rules"})
"""Classifier options that call a model, and so need the gateway."""


def classifier(name: str = DEFAULT_CLASSIFIER, gateway: Completer | None = None) -> Classifier:
    """A registered T5 option by name; ``model_plus_rules`` calls ``gateway`` (``classify``)."""
    if name in MODEL_CLASSIFIERS:
        if gateway is None:
            raise ValueError(f"the {name!r} classifier needs the model gateway")
        built = registry.create("t5_classify", name, gateway=gateway)
    else:
        built = registry.create("t5_classify", name)
    if not isinstance(built, Classifier):
        raise TypeError(f"t5_classify option {name!r} does not implement the Classifier port")
    return built


def classify_step(name: str = DEFAULT_CLASSIFIER, gateway: Completer | None = None) -> ClassifyStep:
    return ClassifyStep(classifier(name, gateway))


def forgetter(
    gateway: ModelGateway | None, *drops: Callable[[str], object]
) -> Callable[[str], int]:
    """T3's ``forget`` for a purged document: every ``drop`` (parsed text, chunks, ...),
    then the gateway's cached replies about it. Returns how many replies were dropped.

    The gateway goes last: if a store fails, T2 records nothing and the purge is
    retried, and dropping cached replies twice is harmless.
    """

    def forget(document_key: str) -> int:
        for drop in drops:
            drop(document_key)
        return gateway.forget(document_key) if gateway is not None else 0

    return forget
