"""Tier B own-baseline detector: an anchored, frozen baseline, gaps never filled, a finding only
for a persistent deviation in the clinically bad direction, and a model card that matches."""

import math
from pathlib import Path

import pytest

from biosentry.domain.computed import NotComputed
from biosentry.domain.scoring.tier_b.baseline import (
    DEFAULTS,
    VITALS,
    BaselineParams,
    Deviation,
    baseline_finding,
)
from biosentry.domain.scoring.tier_b.interface import Finding
from biosentry.schemas import FindingDirection, ProvenanceSource

CARD = Path(__file__).resolve().parents[3] / "models" / "tier_b" / "baseline" / "card.md"
P = DEFAULTS.p


def window(median: float, step: float) -> list[float | None]:
    """24 bins with median `median` and MAD `step`, so the spread is 1.4826 * step."""
    return [median - step, median, median + step] * 8


def found(vital: str, bins: list[float | None]) -> tuple[Finding, Deviation]:
    result = baseline_finding(vital, bins)
    assert not isinstance(result, NotComputed), result
    return result


def test_baseline_forming_reports_how_many_valid_bins_it_has() -> None:
    result = baseline_finding("heart_rate", [70.0] * 9 + [None] * 5)
    assert result == NotComputed("baseline forming, 9 of 24 bins")


@pytest.mark.parametrize("bins", [[], [None] * 40, [math.nan, math.inf, -math.inf] * 10])
def test_no_valid_input_is_still_forming(bins: list[float | None]) -> None:
    assert baseline_finding("spo2", bins) == NotComputed("baseline forming, 0 of 24 bins")


def test_exactly_24_bins_gives_a_baseline() -> None:
    finding, dev = found("heart_rate", window(70, 4))
    assert dev.baseline_median == 70
    assert dev.value == 74 and dev.delta == 4
    assert dev.z == pytest.approx(4 / (1.4826 * 4))
    assert finding.significant is False
    assert finding.direction is FindingDirection.rising
    assert finding.source is ProvenanceSource.derived
    assert finding.id == "baseline/heart_rate"
    assert finding.pathway == "circulatory"


def test_baseline_is_frozen_when_later_values_drift() -> None:
    drift = [70 + 0.5 * i for i in range(1, 60)]
    _, dev = found("heart_rate", window(70, 4) + drift)
    assert dev.baseline_median == 70
    assert dev.delta == pytest.approx(29.5)


def test_floor_stops_a_very_stable_patient_alarming_on_noise() -> None:
    # MAD is 0, so without the 2 bpm floor a 1 bpm wobble would be an infinite z.
    finding, dev = found("heart_rate", [70.0] * 24 + [71.0] * 10)
    assert dev.z == 0.5
    assert dev.run_bins == 0 and dev.since_bin is None
    assert finding.significant is False


@pytest.mark.parametrize(
    ("vital", "bad", "pathway"),
    [
        ("heart_rate", +1, "circulatory"),
        ("respiratory_rate", +1, "respiratory"),
        ("spo2", -1, "respiratory"),
        ("temperature", +1, "temperature"),
    ],
)
def test_each_vital_flags_only_its_bad_direction(vital: str, bad: int, pathway: str) -> None:
    floor = DEFAULTS.floors[vital]
    base = [50.0] * 24  # flat, so the spread is the floor
    shift = (DEFAULTS.k + 1) * floor
    bad_f, bad_d = found(vital, base + [50 + bad * shift] * P)
    good_f, good_d = found(vital, base + [50 - bad * shift] * P)
    assert bad_f.significant and bad_d.run_bins == P and bad_f.pathway == pathway
    assert not good_f.significant and good_d.run_bins == 0
    assert bad_f.direction is (FindingDirection.rising if bad > 0 else FindingDirection.falling)
    assert VITALS[vital].unit in bad_f.value


def test_p_consecutive_bins_are_required_and_a_gap_resets_the_run() -> None:
    base, high = [70.0] * 24, 90.0
    short_f, short_d = found("heart_rate", base + [high] * (P - 1))
    assert not short_f.significant and short_d.run_bins == P - 1
    gapped_f, gapped_d = found("heart_rate", base + [high] * P + [None] + [high] * (P - 1))
    assert not gapped_f.significant and gapped_d.run_bins == P - 1
    bins = base + [high] * P
    full_f, full_d = found("heart_rate", bins)
    assert full_f.significant and full_d.since_bin == len(bins) - P
    assert f"{P} bins" in full_f.value


def test_a_missing_latest_bin_is_not_computed() -> None:
    assert baseline_finding("heart_rate", [70.0] * 24 + [90.0, None]) == NotComputed(
        "latest bin missing"
    )


def test_gaps_inside_the_window_are_skipped_never_filled() -> None:
    # The window is the first 24 valid bins; a gap does not count and is not replaced.
    with_gaps: list[float | None] = []
    for x in window(70, 4):
        with_gaps += [x, None, math.nan]
    _, gapped = found("heart_rate", with_gaps + [80.0])
    _, plain = found("heart_rate", window(70, 4) + [80.0])
    assert (gapped.baseline_median, gapped.z) == (plain.baseline_median, plain.z)
    # The 25th valid bin is outside the window even when gaps came first: distinct values, so
    # a 25-bin window would move the median off 71.5.
    distinct: list[float | None] = [float(x) for x in range(60, 84)]
    _, later = found("heart_rate", [None] * 5 + distinct + [500.0, 75.0])
    assert later.baseline_median == 71.5


def test_non_finite_values_are_treated_as_gaps() -> None:
    assert baseline_finding("heart_rate", [70.0] * 24 + [math.nan]) == NotComputed(
        "latest bin missing"
    )
    _, dev = found("heart_rate", [70.0] * 24 + [90.0] * P + [math.inf] + [90.0])
    assert dev.run_bins == 1


@pytest.mark.parametrize(
    ("vital", "start", "end", "wander_sd"),
    [
        # Spec section 1: a respiratory episode on HR 70, RR 16, SpO2 97 ends at about
        # HR 85.5, RR 23, SpO2 92.5, which NEWS2 scores 4 with no single 3. The full offsets
        # are about 2.5, 2.7 and 2.8 times the fitted wander SD; the baseline spread is set to it.
        ("heart_rate", 70.0, 85.5, 15.5 / 2.5),
        ("respiratory_rate", 16.0, 23.0, 7.0 / 2.7),
        ("spo2", 97.0, 92.5, 4.5 / 2.8),
    ],
)
def test_the_specification_worked_example_is_flagged(
    vital: str, start: float, end: float, wander_sd: float
) -> None:
    finding, dev = found(vital, window(start, wander_sd / 1.4826) + [end] * P)
    assert finding.significant
    assert finding.pathway == "respiratory" or vital == "heart_rate"
    assert abs(dev.z) == pytest.approx(abs(end - start) / wander_sd)
    assert dev.delta == pytest.approx(end - start)


def test_pure_same_input_same_output_and_input_not_mutated() -> None:
    bins: list[float | None] = window(70, 4) + [None, 90.0, 91.0, 92.0]
    before = list(bins)
    assert baseline_finding("heart_rate", bins) == baseline_finding("heart_rate", bins)
    assert bins == before


def test_stable_direction_when_latest_equals_the_median() -> None:
    finding, dev = found("temperature", [37.0] * 25)
    assert finding.direction is FindingDirection.stable and dev.delta == 0


def test_card_states_the_defaults_and_its_limits() -> None:
    text = CARD.read_text(encoding="utf-8")
    assert f"k = {DEFAULTS.k}" in text and f"p = {DEFAULTS.p}" in text
    assert f"{DEFAULTS.window_bins} valid" in text
    for phrase in ("synthetic, not validated", "judgement", "B4", "ONNX", "upper bound"):
        assert phrase in text


def test_at_least_k_is_significant_and_just_under_is_not() -> None:
    # Flat 70, spread is the 2 bpm floor: 74 is z exactly 2.0.
    at_f, at_d = found("heart_rate", [70.0] * 24 + [74.0] * 3)
    assert at_d.z == DEFAULTS.k and at_f.significant
    under_f, _ = found("heart_rate", [70.0] * 24 + [73.99] * 3)
    assert not under_f.significant


def test_the_run_may_reach_back_into_the_baseline_window() -> None:
    # A deviation inside the first six hours is still a deviation from the patient's own start.
    finding, dev = found("heart_rate", [70.0] * 21 + [90.0] * 3)
    assert finding.significant and dev.since_bin == 21 and dev.run_bins == 3


def test_integer_input_is_a_reading() -> None:
    bins: list[float | None] = [70] * 24 + [90] * P  # type: ignore[list-item]
    finding, dev = found("heart_rate", bins)
    assert finding.significant and dev.baseline_median == 70


def test_a_bool_is_not_a_reading_and_counts_as_a_gap() -> None:
    bins: list[float | None] = [70.0] * 23 + [True]
    assert baseline_finding("heart_rate", bins) == NotComputed("baseline forming, 23 of 24 bins")
    assert baseline_finding("heart_rate", [70.0] * 24 + [False]) == NotComputed(
        "latest bin missing"
    )


def test_unknown_vital_is_refused_without_echoing_it() -> None:
    with pytest.raises(ValueError, match="^unknown vital$"):
        baseline_finding("<script>", [70.0] * 30)


@pytest.mark.parametrize(
    ("kw", "error"),
    [
        ({"window_bins": 0}, "window_bins must be"),
        ({"window_bins": -1}, "window_bins must be"),
        ({"p": 0}, "p must be"),
        ({"k": -1.0}, "k must be"),
        ({"k": 0.0}, "k must be"),
        ({"k": math.nan}, "k must be"),
        ({"k": math.inf}, "k must be"),
        ({"floors": {**DEFAULTS.floors, "spo2": 0.0}}, "floors must"),
        ({"floors": {**DEFAULTS.floors, "temperature": math.inf}}, "floors must"),
        ({"floors": {"heart_rate": 2.0}}, "floors must"),
    ],
)
def test_parameters_that_cannot_work_are_refused(kw: dict, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        BaselineParams(**kw)


def test_parameters_are_immutable_and_do_not_share_the_callers_mapping() -> None:
    with pytest.raises(TypeError):
        DEFAULTS.floors["heart_rate"] = 0.5  # type: ignore[index]
    assert DEFAULTS.floors["heart_rate"] == 2.0
    mine = dict(DEFAULTS.floors)
    params = BaselineParams(floors=mine)
    mine["heart_rate"] = 0.5
    assert params.floors["heart_rate"] == 2.0


# Values taken from the detector before the spread was added to `Deviation`. Every old field must
# stay as it was: the band is display only and must not move a finding.
PINNED = {
    "hr_stable": (
        "heart_rate", window(70, 4),
        ("baseline/heart_rate", "Heart rate vs own baseline", "+4.0 bpm (median 70.0, z +0.7, 0 bins)",
         "rising", "derived", False, "circulatory", 74, 70.0, 4.0, 0.6744907594765952, 0, None),
    ),
    "hr_floor": (
        "heart_rate", [70.0] * 24 + [71.0] * 10,
        ("baseline/heart_rate", "Heart rate vs own baseline", "+1.0 bpm (median 70.0, z +0.5, 0 bins)",
         "rising", "derived", False, "circulatory", 71.0, 70.0, 1.0, 0.5, 0, None),
    ),
    "hr_flagged": (
        "heart_rate", [70.0] * 24 + [90.0] * 3,
        ("baseline/heart_rate", "Heart rate vs own baseline", "+20.0 bpm (median 70.0, z +10.0, 3 bins)",
         "rising", "derived", True, "circulatory", 90.0, 70.0, 20.0, 10.0, 3, 24),
    ),
    "hr_gap_run": (
        "heart_rate", [70.0] * 24 + [90.0] * 3 + [None] + [90.0] * 2,
        ("baseline/heart_rate", "Heart rate vs own baseline", "+20.0 bpm (median 70.0, z +10.0, 2 bins)",
         "rising", "derived", False, "circulatory", 90.0, 70.0, 20.0, 10.0, 2, 28),
    ),
    "rr_wide": (
        "respiratory_rate", window(16, 1.5) + [23.0] * 3,
        ("baseline/respiratory_rate", "Respiratory rate vs own baseline",
         "+7.0 /min (median 16.0, z +3.1, 3 bins)",
         "rising", "derived", True, "respiratory", 23.0, 16.0, 7.0, 3.147623544224111, 3, 24),
    ),
    "spo2_fall": (
        "spo2", window(97, 0.8) + [92.5] * 3,
        ("baseline/spo2", "SpO2 vs own baseline", "-4.5 % (median 97.0, z -3.8, 3 bins)",
         "falling", "derived", True, "respiratory", 92.5, 97.0, -4.5, -3.7940105220558613, 3, 24),
    ),
    "spo2_rise": (
        "spo2", [97.0] * 24 + [99.0] * 4,
        ("baseline/spo2", "SpO2 vs own baseline", "+2.0 % (median 97.0, z +2.0, 0 bins)",
         "rising", "derived", False, "respiratory", 99.0, 97.0, 2.0, 2.0, 0, None),
    ),
    "hr_back_in_window": (
        "heart_rate", [70.0] * 21 + [90.0] * 3,
        ("baseline/heart_rate", "Heart rate vs own baseline", "+20.0 bpm (median 70.0, z +10.0, 3 bins)",
         "rising", "derived", True, "circulatory", 90.0, 70.0, 20.0, 10.0, 3, 21),
    ),
    "temp_flat": (
        "temperature", [37.0] * 25,
        ("baseline/temperature", "Temperature vs own baseline", "+0.0 °C (median 37.0, z +0.0, 0 bins)",
         "stable", "derived", False, "temperature", 37.0, 37.0, 0.0, 0.0, 0, None),
    ),
}


@pytest.mark.parametrize("name", list(PINNED))
def test_the_finding_and_every_old_deviation_field_are_unchanged(name: str) -> None:
    vital, bins, expected = PINNED[name]
    finding, dev = found(vital, bins)
    old = (
        finding.id, finding.label, finding.value, finding.direction.value, finding.source.value,
        finding.significant, finding.pathway,
        dev.value, dev.baseline_median, dev.delta, dev.z, dev.run_bins, dev.since_bin,
    )
    assert old == expected


def test_the_spread_is_the_one_behind_z() -> None:
    # MAD 4 gives 1.4826 * 4; a flat window gives the 2 bpm floor.
    _, wide = found("heart_rate", window(70, 4))
    _, flat = found("heart_rate", [70.0] * 25)
    assert wide.spread == pytest.approx(1.4826 * 4)
    assert flat.spread == 2.0
    assert wide.z == pytest.approx(wide.delta / wide.spread)


def test_a_value_at_the_band_edge_has_z_k_from_the_same_centre_and_spread() -> None:
    _, dev = found("heart_rate", window(70, 4))
    high = dev.baseline_median + DEFAULTS.k * dev.spread
    low = dev.baseline_median - DEFAULTS.k * dev.spread
    assert (round(low, 1), round(high, 1)) == (58.1, 81.9)
    _, at_high = found("heart_rate", window(70, 4) + [high])
    _, at_low = found("heart_rate", window(70, 4) + [low])
    assert at_high.z == pytest.approx(DEFAULTS.k)
    assert at_low.z == pytest.approx(-DEFAULTS.k)
