import pytest

from ekb.platform import registry


class _Dummy:
    def __init__(self, size: int = 1) -> None:
        self.size = size


def test_create_passes_params() -> None:
    registry.register("test_step_create", "dummy")(_Dummy)
    instance = registry.create("test_step_create", "dummy", size=3)
    assert isinstance(instance, _Dummy)
    assert instance.size == 3


def test_unknown_option_lists_alternatives() -> None:
    registry.register("test_step_unknown", "dummy")(_Dummy)
    with pytest.raises(registry.UnknownOptionError, match="available: dummy"):
        registry.create("test_step_unknown", "missing")


def test_name_clash_is_rejected() -> None:
    registry.register("test_step_clash", "dummy")(_Dummy)

    class Other:
        pass

    with pytest.raises(ValueError, match="already registered"):
        registry.register("test_step_clash", "dummy")(Other)
