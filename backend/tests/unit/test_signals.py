"""Windowed signals and the Tier B scaffold: invalid input is refused, thin windows are refused,
partial windows are degraded, nothing is filled in, and the scaffold labeller fabricates nothing."""

import math
from collections.abc import Sequence

import pytest

from biosentry.domain.computed import Computed, NotComputed
from biosentry.domain.scoring.tier_b.interface import (
    LabellerRegistry,
    Unimplemented,
    label_windows,
)
from biosentry.domain.signals import SignalInput, apply_windows
from biosentry.schemas import Provenance, QualityLevel
from samples import model_provenance  # isort: skip

PROV = Provenance.model_validate(model_provenance("B", "m"))


def signal(samples: Sequence[float], valid: Sequence[bool], fs: float = 1, window_s: float = 2):
    return SignalInput(fs=fs, window_s=window_s, samples=tuple(samples), valid=tuple(valid))


@pytest.mark.parametrize(
    ("kw", "error"),
    [
        ({"fs": 0}, "fs must be"),
        ({"fs": math.nan}, "fs must be"),
        ({"window_s": -1}, "window_s must be"),
        ({"window_s": math.inf}, "window_s must be"),
        ({"valid": (True,)}, "same length"),
        ({"fs": 1, "window_s": 0.4}, "at least one sample"),
    ],
)
def test_a_signal_that_cannot_be_windowed_is_refused(kw: dict, error: str) -> None:
    args = {"fs": 1.0, "window_s": 2.0, "samples": (1.0, 2.0), "valid": (True, True), **kw}
    with pytest.raises(ValueError, match=error):
        SignalInput(**args)


def test_a_nan_sample_is_never_usable_even_when_the_mask_says_valid() -> None:
    assert signal([1, math.nan, 3], [True, True, False]).usable == (True, False, False)


def test_windows_are_ok_degraded_or_refused_by_coverage_and_the_tail_is_dropped() -> None:
    seen: list[tuple[tuple[float, ...], tuple[bool, ...]]] = []

    def mean_of_usable(samples: Sequence[float], usable: Sequence[bool]) -> float:
        seen.append((tuple(samples), tuple(usable)))
        return sum(x for x, u in zip(samples, usable, strict=True) if u) / sum(usable)

    s = signal([2, 4, 6, 99, 7, 8, 5], [True, True, True, False, False, False, True])
    full, partial, empty = apply_windows(s, mean_of_usable, provenance=PROV)  # 7th sample: no window

    assert isinstance(full, Computed) and full.value == 3 and full.quality is QualityLevel.ok
    assert (full.window.start_s, full.window.end_s, full.window.coverage) == (0, 2, 1)
    assert isinstance(partial, Computed) and partial.value == 6
    assert partial.quality is QualityLevel.degraded and partial.window.coverage == 0.5
    assert empty == NotComputed("coverage 0.00 below 0.50")
    # the invalid 99 reaches the function with its mask, never dropped or replaced
    assert seen == [((2, 4), (True, True)), ((6, 99), (True, False))]
    assert PROV.quality is QualityLevel.ok  # the caller's provenance is copied, not changed


def test_a_function_may_refuse_a_window_itself() -> None:
    refuse = NotComputed("flat line")
    assert apply_windows(signal([1, 1], [True, True]), lambda *_: refuse, provenance=PROV) == (
        refuse,
    )


@pytest.mark.parametrize("coverage", [0, -0.1, 1.01])
def test_min_coverage_must_be_a_fraction(coverage: float) -> None:
    with pytest.raises(ValueError, match="min_coverage"):
        apply_windows(signal([1, 1], [True, True]), lambda *_: 1, provenance=PROV, min_coverage=coverage)


def test_the_scaffold_labeller_labels_nothing() -> None:
    labeller = Unimplemented("af")
    results = label_windows(labeller, signal([1, 2, 3, 4], [True] * 4), PROV)
    assert labeller.id == "af"
    assert results == (NotComputed("not_implemented"),) * 2


def test_the_registry_holds_each_labeller_once() -> None:
    registry = LabellerRegistry()
    af, sinus = Unimplemented("af"), Unimplemented("sinus")
    registry.register(sinus)
    registry.register(af)
    assert registry.get("af") is af
    assert registry.ids() == ("af", "sinus")
    with pytest.raises(ValueError, match="already registered"):
        registry.register(Unimplemented("af"))
    with pytest.raises(KeyError, match="no labeller registered as 'vt'"):
        registry.get("vt")
