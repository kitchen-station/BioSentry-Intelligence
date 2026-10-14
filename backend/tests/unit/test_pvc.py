"""The PVC metric code and the RR-ratio comparator (training/pvc.py), development side only.

Numpy at import, so every pure function is exercised here from hand-built beats and synthetic ECG;
the command that reads the MIT-BIH development records needs the corpus and is not run. What is
pinned: beat context and its edges, the strict inequalities of the rules, one-to-one matching,
the counts that define sensitivity, PPV and specificity (V is the only positive, a missed beat is
not a call, a spurious detection called PVC costs PPV), pooled rates with zero-PVC records kept in
the denominators, and the beats lost to where the window edges fell.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest
from synth_ecg import ecg_with_premature_beats, synth_ecg

ROOT = Path(__file__).resolve().parents[3]
TRAINING = ROOT / "training"
FS = 250
NAN = float("nan")


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, TRAINING / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rb = _load("rr_baseline")
pvc = _load("pvc")


def _detections(
    pos: list[int], covered: tuple[int, int] = (0, 10_000), window: list[int] | None = None
) -> object:
    n = len(pos)
    return pvc.Detections(
        np.array(pos, dtype=np.int64),
        np.full(n, NAN),
        np.full(n, NAN),
        np.full(n, NAN),
        np.array(window if window is not None else [0] * n, dtype=np.int64),
        covered,
    )


# --- context and the rules ------------------------------------------------------------------


def test_the_context_of_a_window_is_the_neighbouring_intervals_and_the_median() -> None:
    before, after, median = pvc.window_context(np.array([100, 300, 500, 750, 1000]))
    assert np.allclose(before, [NAN, 0.8, 0.8, 1.0, 1.0], equal_nan=True)
    assert np.allclose(after, [0.8, 0.8, 1.0, 1.0, NAN], equal_nan=True)
    assert median == pytest.approx(0.9)


def test_the_median_interval_is_a_median_not_a_mean() -> None:
    # intervals 0.8 0.8 0.8 1.6 s: one long pause moves the mean to 0.96 and leaves the median
    _, _, median = pvc.window_context(np.array([100, 300, 500, 700, 1100]))
    assert median == pytest.approx(0.8)


def test_four_beats_are_enough_for_a_median_and_two_give_one_interval_each_way() -> None:
    before, after, median = pvc.window_context(np.array([100, 300, 500, 700]))
    assert median == pytest.approx(0.8)
    assert np.allclose(before, [NAN, 0.8, 0.8, 0.8], equal_nan=True)
    before, after, median = pvc.window_context(np.array([100, 350]))
    assert np.allclose(before, [NAN, 1.0], equal_nan=True)
    assert np.allclose(after, [1.0, NAN], equal_nan=True)
    assert np.isnan(median)


def test_a_window_with_three_beats_has_intervals_but_no_median_and_fewer_have_neither() -> None:
    before, after, median = pvc.window_context(np.array([100, 300, 500]))
    assert np.allclose(before, [NAN, 0.8, 0.8], equal_nan=True)
    assert np.allclose(after, [0.8, 0.8, NAN], equal_nan=True)
    assert np.isnan(median)  # a median of two intervals would be an interval, not a context
    before, after, median = pvc.window_context(np.array([100]))
    assert np.isnan(before).all()
    assert np.isnan(after).all()
    assert np.isnan(median)
    before, after, median = pvc.window_context(np.zeros(0, dtype=np.int64))
    assert before.size == after.size == 0
    assert np.isnan(median)


def test_the_ratio_rule_is_strict_and_never_calls_a_beat_without_context() -> None:
    prev = np.array([0.74, 0.75, 0.76, NAN, 0.5])
    median = np.array([1.0, 1.0, 1.0, 1.0, NAN])
    assert pvc.rr_ratio_rule(prev, median, 0.75).tolist() == [True, False, False, False, False]


def test_the_pause_rule_wants_both_a_short_and_a_long_interval_and_is_strict() -> None:
    prev = np.array([0.6, 0.6, 0.6, 0.9, NAN, 0.6])
    nxt = np.array([1.3, 1.2, 1.1, 1.5, 1.5, NAN])
    median = np.ones(6)
    assert pvc.rr_pause_rule(prev, nxt, median, 0.75, 1.2).tolist() == [
        True,  # premature and a pause
        False,  # the pause is exactly phi: strict
        False,  # premature, no pause
        False,  # a pause, not premature
        False,  # no interval before
        False,  # no interval after
    ]


# --- the grid of independent windows -----------------------------------------------------------


def test_the_grid_is_independent_windows_with_context_that_stops_at_their_edges() -> None:
    x = np.array(synth_ecg(duration_s=30.0, seed=9).samples)[:6200]
    det = pvc.detect_grid(x, fraction=0.25)
    assert det.pos.tolist() == rb.detect_windowed(x).tolist()
    assert det.covered == (0, 5000)  # the 1200 samples left over are not a window
    firsts = [int(np.flatnonzero(det.window == w)[0]) for w in np.unique(det.window)]
    lasts = [int(np.flatnonzero(det.window == w)[-1]) for w in np.unique(det.window)]
    assert np.isnan(det.rr_prev[firsts]).all()
    assert np.isnan(det.rr_next[lasts]).all()
    inside = np.ones(len(det.pos), dtype=bool)
    inside[firsts] = False
    assert np.isfinite(det.rr_prev[inside]).all()


def test_a_shifted_grid_covers_the_shifted_range_and_a_lower_fraction_finds_weaker_beats() -> None:
    x = np.array(synth_ecg(duration_s=40.0, seed=2).samples)
    shifted = pvc.detect_grid(x, start=1250)
    assert shifted.covered == (1250, 1250 + 3 * 2500)
    assert shifted.pos.min() >= 1250
    assert shifted.pos.max() < 1250 + 3 * 2500
    t = np.arange(2500) / FS
    window = np.zeros(2500)
    for k in range(10):
        amplitude = 0.35 if k in (3, 6) else 1.0  # two beats at about 12% of the typical energy
        window += amplitude * np.exp(-0.5 * ((t - (0.5 + 0.9 * k)) / 0.012) ** 2)
    assert rb.detect_r_peaks(window).size == 8
    assert rb.detect_r_peaks(window, threshold_fraction=0.05).size == 10
    assert pvc.detect_grid(window, fraction=0.25).pos.size == 8  # the grid hands its fraction on
    assert pvc.detect_grid(window, fraction=0.05).pos.size == 10


def test_an_empty_grid_has_no_detections_and_still_says_what_it_covers() -> None:
    det = pvc.detect_grid(np.zeros(1000))
    assert det.pos.size == 0
    assert det.covered == (0, 0)


# --- matching -------------------------------------------------------------------------------


def test_matching_indices_pair_each_reference_and_detection_at_most_once() -> None:
    to_det, to_ref = pvc.match_indices(
        np.array([100.0, 200.0, 300.0]), np.array([104.0, 105.0, 290.0, 500.0]), 10.0
    )
    assert to_det.tolist() == [0, -1, 2]  # 105 is a second detection of the first beat
    assert to_ref.tolist() == [0, -1, 2, -1]
    # the tolerance is inclusive on both sides
    to_det, _ = pvc.match_indices(np.array([100.0, 300.0]), np.array([110.0, 290.0]), 10.0)
    assert to_det.tolist() == [0, 1]
    to_det, _ = pvc.match_indices(np.array([100.0, 300.0]), np.array([110.5, 289.5]), 10.0)
    assert to_det.tolist() == [-1, -1]


def test_matching_indices_agree_with_the_counting_matcher_and_are_inverse() -> None:
    rng = np.random.default_rng(5)
    for _ in range(40):
        ref = np.sort(rng.uniform(0, 5000, 30))
        det = np.sort(ref + rng.normal(0, 25, 30))[rng.random(30) < 0.9]
        to_det, to_ref = pvc.match_indices(ref, det, 37.5)
        tp, fp, fn = rb.match_beats(ref, det, 37.5)
        assert (to_det >= 0).sum() == tp
        assert (to_ref < 0).sum() == fp
        assert (to_det < 0).sum() == fn
        for i, j in enumerate(to_det):
            if j >= 0:
                assert to_ref[j] == i
                assert abs(det[j] - ref[i]) <= 37.5


def test_the_table_scores_only_beats_inside_the_windows_that_were_evaluated() -> None:
    det = _detections([100, 350], covered=(0, 1000))
    table = pvc.build_table(
        np.array([2000.0, 350.0, 100.0, 999.0, 1000.0, 0.0]), np.array(list("VVNNVF")), det
    )
    # sorted; the beat at 0 is inside [0, 1000), the one at 1000 is not
    assert table.ref_pos.tolist() == [0.0, 100.0, 350.0, 999.0]
    assert table.ref_symbol.tolist() == ["F", "N", "V", "N"]
    assert table.det_to_ref.tolist() == [1, 2]


# --- the counts -----------------------------------------------------------------------------


def _scored_table() -> object:
    ref_pos = np.array([100.0, 350.0, 600.0, 850.0, 1100.0, 1350.0])
    ref_sym = np.array(["N", "V", "N", "V", "F", "V"])
    det = _detections([102, 352, 598, 1099, 1351, 2000], covered=(0, 2500))
    return pvc.build_table(ref_pos, ref_sym, det)


def test_counts_separate_misses_wrong_calls_and_spurious_detections() -> None:
    table = _scored_table()
    called = np.array([False, True, True, False, True, True])
    got = pvc.score_called(table, called)
    assert (got["n_v"], got["n_nonv"]) == (3, 3)
    assert got["tp"] == 2  # the V at 350 and the V at 1350
    assert got["fn_missed"] == 1  # the V at 850: no detection reached it
    assert got["fn_called_normal"] == 0
    assert got["fp_matched"] == 1
    assert got["fp_by_symbol"] == {"N": 1}  # the normal beat at 600
    assert got["fp_spurious"] == 1  # the detection at 2000 matched nothing and was called
    assert (got["detections"], got["called"]) == (6, 4)
    assert got["tp"] + got["fn_called_normal"] + got["fn_missed"] == got["n_v"]


def test_a_matched_v_beat_not_called_is_a_called_normal_and_f_is_a_negative() -> None:
    table = _scored_table()
    called = np.array([False, False, False, True, False, False])  # the fusion beat gets called
    got = pvc.score_called(table, called)
    assert got["tp"] == 0
    assert got["fn_called_normal"] == 2  # the V at 350 and the V at 1350, detected and not called
    assert got["fn_missed"] == 1
    assert got["fp_by_symbol"] == {"F": 1}
    assert got["fp_matched"] == 1
    assert got["fp_spurious"] == 0
    assert got["tp"] + got["fn_called_normal"] + got["fn_missed"] == got["n_v"]


def test_a_wrong_call_on_the_first_annotated_beat_is_a_matched_false_positive() -> None:
    table = _scored_table()
    called = np.array([True, False, False, False, False, False])
    got = pvc.score_called(table, called)
    assert got["fp_matched"] == 1
    assert got["fp_by_symbol"] == {"N": 1}
    assert got["fp_spurious"] == 0


def test_nothing_called_means_no_true_or_false_positives() -> None:
    got = pvc.score_called(_scored_table(), np.zeros(6, dtype=bool))
    assert (got["tp"], got["fp_matched"], got["fp_spurious"], got["called"]) == (0, 0, 0, 0)
    assert got["fp_by_symbol"] == {}


def test_a_record_with_no_beats_or_no_detections_scores_zero_without_error() -> None:
    empty = pvc.build_table(
        np.zeros(0), np.array([], dtype="<U1"), _detections([], covered=(0, 100))
    )
    got = pvc.score_called(empty, np.zeros(0, dtype=bool))
    assert (got["n_v"], got["n_nonv"], got["tp"], got["detections"]) == (0, 0, 0, 0)
    none = pvc.build_table(
        np.array([10.0, 50.0]), np.array(["V", "N"]), _detections([], covered=(0, 100))
    )
    got = pvc.score_called(none, np.zeros(0, dtype=bool))
    assert (got["n_v"], got["n_nonv"], got["fn_missed"]) == (1, 1, 1)


# --- beats lost to where the windows fell ---------------------------------------------------


def test_beats_missed_on_the_grid_but_found_on_the_shifted_one_are_counted() -> None:
    ref_pos = np.array([300.0, 2495.0, 2800.0, 4000.0, 9000.0])
    ref_sym = np.array(["V", "V", "V", "V", "V"])
    primary = _detections([300, 2800, 9000], covered=(0, 10_000))
    table = pvc.build_table(ref_pos, ref_sym, primary)
    # shifted grid covers [1250, 6250): it finds 2495 (the beat the edge cut) and 4000 (found
    # nowhere else); it cannot see 300 or 9000, which the primary grid already detected
    shifted = _detections([2497, 4001], covered=(1250, 6250))
    assert pvc.placement_misses(table, shifted) == 2
    nothing = _detections([], covered=(1250, 6250))
    assert pvc.placement_misses(table, nothing) == 0


def test_the_first_annotated_beat_and_the_edges_of_the_shifted_range_are_handled() -> None:
    ref_pos = np.array([1250.0, 2000.0, 6249.0, 6250.0])
    table = pvc.build_table(ref_pos, np.array(["V"] * 4), _detections([2000], covered=(0, 10_000)))
    # the detected V at 2000 is found already; 1250 is the first sample the shifted grid covers,
    # 6249 its last, 6250 is outside: two recoverable, one not
    shifted = _detections([1250, 2000, 6249, 6250], covered=(1250, 6250))
    assert pvc.placement_misses(table, shifted) == 2
    first = pvc.build_table(
        np.array([300.0]), np.array(["V"]), _detections([300], covered=(0, 10_000))
    )
    assert pvc.placement_misses(first, _detections([300], covered=(0, 10_000))) == 0


def test_a_beat_the_shifted_grid_does_not_cover_cannot_be_recovered() -> None:
    table = pvc.build_table(
        np.array([100.0, 7000.0]), np.array(["V", "V"]), _detections([], covered=(0, 10_000))
    )
    shifted = _detections([101, 7001], covered=(1250, 6250))  # detections outside are ignored
    assert pvc.placement_misses(table, shifted) == 0


def test_only_v_beats_count_as_placement_misses() -> None:
    table = pvc.build_table(
        np.array([2495.0, 2600.0]), np.array(["N", "F"]), _detections([], covered=(0, 10_000))
    )
    assert pvc.placement_misses(table, _detections([2497, 2601], covered=(1250, 6250))) == 0


# --- pooled rates and the bootstrap statistics ------------------------------------------------


def _record(n_v: int, n_nonv: int, **methods: dict[str, int]) -> dict[str, object]:
    return {"n_v": n_v, "n_nonv": n_nonv, "methods": methods}


def test_rates_are_pooled_over_records_not_averaged_and_zero_pvc_records_stay_in() -> None:
    records = [
        _record(10, 90, rule={"tp": 8, "fp_matched": 2, "fp_spurious": 0}),
        _record(0, 100, rule={"tp": 0, "fp_matched": 10, "fp_spurious": 0}),  # no PVCs at all
        _record(90, 10, rule={"tp": 45, "fp_matched": 0, "fp_spurious": 5}),
    ]
    got = pvc.pooled(records, "rule")
    assert got["sensitivity"] == pytest.approx(53 / 100)
    assert got["ppv"] == pytest.approx(53 / (53 + 12 + 5))
    assert got["specificity"] == pytest.approx(1 - 12 / 200)  # spurious detections have no beat
    assert got["f1"] == pytest.approx(2 * 53 / (2 * 53 + 17 + 47))


def test_undefined_rates_are_nan_not_zero() -> None:
    nothing = [_record(0, 0, rule={"tp": 0, "fp_matched": 0, "fp_spurious": 0})]
    got = pvc.pooled(nothing, "rule")
    assert all(np.isnan(got[k]) for k in ("sensitivity", "ppv", "specificity", "f1"))
    never_called = [_record(5, 5, rule={"tp": 0, "fp_matched": 0, "fp_spurious": 0})]
    got = pvc.pooled(never_called, "rule")
    assert got["sensitivity"] == 0.0
    assert np.isnan(got["ppv"])
    assert got["specificity"] == 1.0


def test_statistics_carry_each_method_and_the_paired_differences() -> None:
    records = [
        _record(
            10,
            90,
            head={"tp": 9, "fp_matched": 1, "fp_spurious": 0},
            rule={"tp": 6, "fp_matched": 6, "fp_spurious": 0},
        ),
        _record(
            10,
            90,
            head={"tp": 8, "fp_matched": 3, "fp_spurious": 1},
            rule={"tp": 5, "fp_matched": 9, "fp_spurious": 1},
        ),
    ]
    got = pvc.pvc_statistics(records, ["head", "rule"], [("head", "rule")])
    assert got["head_sensitivity"] == pytest.approx(17 / 20)
    assert got["rule_sensitivity"] == pytest.approx(11 / 20)
    assert got["head_ppv"] == pytest.approx(17 / 22)
    assert got["rule_ppv"] == pytest.approx(11 / 27)
    assert got["delta_head_minus_rule_sensitivity"] == pytest.approx(6 / 20)
    assert got["delta_head_minus_rule_ppv"] == pytest.approx(17 / 22 - 11 / 27)
    assert got["delta_head_minus_rule_specificity"] == pytest.approx((1 - 4 / 180) - (1 - 15 / 180))
    assert set(got) == {
        f"{m}_{k}" for m in ("head", "rule") for k in ("sensitivity", "ppv", "specificity")
    } | {f"delta_head_minus_rule_{k}" for k in ("sensitivity", "ppv", "specificity")}


def test_the_record_bootstrap_of_the_counts_is_the_pinned_resampler() -> None:
    records = [
        _record(10, 90, rule={"tp": 8, "fp_matched": 2, "fp_spurious": 0}),
        _record(20, 80, rule={"tp": 10, "fp_matched": 5, "fp_spurious": 1}),
        _record(0, 100, rule={"tp": 0, "fp_matched": 4, "fp_spurious": 0}),
    ]

    def stats(recs: list[dict[str, object]]) -> dict[str, float]:
        got: dict[str, float] = pvc.pvc_statistics(recs, ["rule"], [])
        return got

    got = rb.record_bootstrap(records, stats, n_boot=20, seed=rb.SEED)
    rng = np.random.default_rng(rb.SEED)
    draws = [
        pvc.pooled([records[i] for i in rng.integers(0, 3, 3)], "rule")["sensitivity"]
        for _ in range(20)
    ]
    assert got["rule_sensitivity"]["point"] == pytest.approx(18 / 30)
    assert got["rule_sensitivity"]["lo"] == pytest.approx(np.nanpercentile(draws, 2.5))
    assert got["rule_sensitivity"]["hi"] == pytest.approx(np.nanpercentile(draws, 97.5))


def test_the_detector_fraction_is_the_one_that_finds_most_v_beats_above_the_ppv_floor() -> None:
    quality = {
        0.05: {"beat_ppv": 0.985, "v_found": 0.99},
        0.10: {"beat_ppv": 0.990, "v_found": 0.97},  # exactly on the floor: allowed
        0.15: {"beat_ppv": 0.992, "v_found": 0.97},  # tie on V found: the larger fraction
        0.20: {"beat_ppv": 0.994, "v_found": 0.90},
    }
    assert pvc.choose_fraction(quality) == 0.15
    assert pvc.choose_fraction({0.1: {"beat_ppv": 0.99, "v_found": 0.5}}) == 0.1
    with pytest.raises(ValueError, match="no detector fraction"):
        pvc.choose_fraction({0.1: {"beat_ppv": 0.98, "v_found": 1.0}})


def test_a_tie_in_a_grid_goes_to_the_first_entry_and_the_best_is_not_the_last() -> None:
    assert pvc.first_best({0.5: 0.2, 0.6: 0.4, 0.7: 0.4, 0.8: 0.1}) == 0.6
    assert pvc.first_best({(0.5, 1.0): 0.3, (0.5, 1.1): 0.5, (0.6, 1.0): 0.5}) == (0.5, 1.1)


def test_detection_quality_counts_matched_detections_and_v_beats_reached() -> None:
    table = _scored_table()
    got = pvc.detection_quality([table])
    assert got["beat_ppv"] == pytest.approx(5 / 6)  # the detection at 2000 matched nothing
    assert got["v_found"] == pytest.approx(2 / 3)  # the V at 850 was not reached
    assert np.isnan(pvc.detection_quality([])["beat_ppv"])
    # more normal than V beats, and the first annotated beat is a V that was reached
    ref_pos = np.array([100.0, 300.0, 500.0, 700.0, 900.0])
    ref_sym = np.array(["V", "N", "N", "N", "V"])
    det = _detections([101, 299, 501, 3000], covered=(0, 5000))
    got = pvc.detection_quality([pvc.build_table(ref_pos, ref_sym, det)])
    assert got["beat_ppv"] == pytest.approx(3 / 4)
    assert got["v_found"] == pytest.approx(1 / 2)


def test_symbol_counts_are_summed_across_records() -> None:
    assert pvc._merge_counts([{"N": 2, "A": 1}, {}, {"N": 3, "F": 1}]) == {"N": 5, "A": 1, "F": 1}
    assert pvc._merge_counts([]) == {}


# --- end to end on synthetic ECG --------------------------------------------------------------


def test_the_rule_calls_every_premature_synthetic_beat_and_no_normal_one() -> None:
    # the premature beats arrive at 13.0, 34.3, 55.5 and 81.0 s: clear of the window edges
    x, pos, sym = ecg_with_premature_beats((15, 40, 65, 95))
    det = pvc.detect_grid(x, fraction=0.15)
    table = pvc.build_table(pos, sym, det)
    called = pvc.rr_ratio_rule(det.rr_prev, det.median_rr, 0.8)
    got = pvc.score_called(table, called)
    assert got["n_v"] == 4
    assert got["tp"] == 4
    assert got["fp_matched"] == 0
    assert got["fp_spurious"] == 0
    assert got["fn_missed"] == 0


def test_the_pause_variant_calls_the_same_beats_and_a_pause_alone_calls_nothing() -> None:
    x, pos, sym = ecg_with_premature_beats((15, 40, 65, 95))
    det = pvc.detect_grid(x, fraction=0.15)
    table = pvc.build_table(pos, sym, det)
    called = pvc.rr_pause_rule(det.rr_prev, det.rr_next, det.median_rr, 0.8, 1.2)
    assert pvc.score_called(table, called)["tp"] == 4  # the next interval is 1.4 RR
    assert pvc.score_called(table, called)["fp_matched"] == 0
    stricter = pvc.rr_pause_rule(det.rr_prev, det.rr_next, det.median_rr, 0.8, 1.5)
    assert pvc.score_called(table, stricter)["tp"] == 0


def test_a_premature_beat_at_the_start_of_a_window_cannot_be_called() -> None:
    # sinus beats sit 0.85 s apart from 0.6 s: beat 12 is the first one at or after 10.0 s
    x, pos, sym = ecg_with_premature_beats((12,))
    det = pvc.detect_grid(x, fraction=0.15)
    table = pvc.build_table(pos, sym, det)
    k = int(np.argmin(np.abs(det.pos - pos[12])))
    assert det.window[k] != det.window[k - 1]  # first beat of its window
    called = pvc.rr_ratio_rule(det.rr_prev, det.median_rr, 0.8)
    got = pvc.score_called(table, called)
    assert got["tp"] == 0
    assert got["fn_called_normal"] == 1  # detected, with no interval before it: not a call


# --- what is frozen -----------------------------------------------------------------------------


def test_the_development_grids_and_detector_choice_are_the_documented_ones() -> None:
    assert pvc.THETAS[0] == 0.50
    assert pvc.THETAS[-1] == 0.95
    assert len(pvc.THETAS) == 46
    assert pvc.PHIS[0] == 1.00
    assert pvc.PHIS[-1] == 1.40
    assert len(pvc.PHIS) == 9
    assert pvc.FRACTIONS == (0.05, 0.10, 0.15, 0.20, 0.25)
    assert pvc.DETECTOR_FRACTION == 0.15
    assert pvc.DETECTOR_FRACTION in pvc.FRACTIONS
    assert pvc.MIN_BEAT_PPV == 0.99
    assert pvc.MATCH_TOL_S == 0.150
    assert pvc.MIN_CONTEXT_BEATS == 4
    assert pvc.POSITIVE == "V"


def test_the_development_code_can_reach_ds1_and_nothing_else() -> None:
    assert len(rb.DS1) == 22
    ds2 = [
        *("100", "103", "105", "111", "113", "117", "121", "123", "200", "202", "210"),
        *("212", "213", "214", "219", "221", "222", "228", "231", "232", "233", "234"),
    ]
    paced = ("102", "104", "107", "217")
    for path in sorted(TRAINING.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert not [r for r in ds2 + list(paced) if f'"{r}"' in text], path.name
    assert not set(ds2) & set(rb.DS1)
