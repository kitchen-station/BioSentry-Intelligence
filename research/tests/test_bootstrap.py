import pytest

from research.bootstrap import RESAMPLES, SEED, SplitMix64, intervals

SUM_RATIO = {"rate": lambda s: s[0] / s[1] if s[1] else None}


def test_splitmix64_known_vectors():
    g = SplitMix64(0)  # the published reference outputs for seed 0
    assert [g.next() for _ in range(3)] == [0xE220A8397B1DCDAF, 0x6E789E6AA1B965F4, 0x06C45D188009454F]


def test_the_fixed_settings_are_documented_numbers():
    assert (RESAMPLES, SEED) == (1000, 20261112)


def test_identical_patients_give_a_degenerate_interval():
    units = {"p": [(2, 4)] * 7}
    assert intervals(units, SUM_RATIO)["p", "rate"] == (0.5, 0.5)


def test_interval_is_deterministic_and_inside_the_patient_range():
    units = {"p": [(a, 1) for a in (0, 0, 1, 1, 2, 5, 0, 3)]}
    first = intervals(units, SUM_RATIO)["p", "rate"]
    assert first == intervals(units, SUM_RATIO)["p", "rate"]
    assert 0 <= first[0] <= 1.5 <= first[1] <= 5


def test_undefined_resamples_are_skipped_and_no_patients_give_none():
    assert intervals({"p": []}, SUM_RATIO)["p", "rate"] is None
    assert intervals({"p": [(0, 0)] * 3}, SUM_RATIO)["p", "rate"] is None


def test_policies_share_the_same_resamples():
    units = {"a": [(1, 1), (0, 1), (1, 1)], "b": [(1, 1), (0, 1), (1, 1)]}
    out = intervals(units, SUM_RATIO)
    assert out["a", "rate"] == out["b", "rate"]
