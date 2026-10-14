"""The PVC head's numpy side (training/pvc_head.py): preprocessing, snippets, the twelve beat
features, the boosted-tree forward pass, the export of fitted trees and the record-grouped folds.
The training command needs scikit-learn and the corpus and is not run here; the weights it wrote
are tested in test_pvc_head_artifacts.py.

Preprocessing is checked against the backend's own filter and normaliser, written independently:
a window must come out of both the same.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest
from synth_ecg import ecg_with_premature_beats

from biosentry.domain.scoring.tier_b import rhythm_cnn as rc

ROOT = Path(__file__).resolve().parents[3]
TRAINING = ROOT / "training"
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
head = _load("pvc_head")
WEIGHTS = rc.load_weights()  # the arrhythmia head's weights: only its band-pass is used here
SOS = np.asarray(WEIGHTS["sos"], dtype=np.float64)


# --- preprocessing --------------------------------------------------------------------------


def _windows(n: int = 3, seed: int = 4) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(2500) / 250
    base = np.sin(2 * np.pi * 1.2 * t) + 0.3 * np.sin(2 * np.pi * 0.2 * t)
    return np.stack([base * (i + 1) + rng.normal(0, 0.1, 2500) + 5 * i for i in range(n)])


def test_the_batch_filter_is_the_backends_filter_row_by_row() -> None:
    raw = _windows()
    got = head.sos_filter(raw, SOS)
    for i, row in enumerate(raw):
        expected = rc.sos_filter(SOS, row.astype(np.float32))
        assert np.allclose(got[i], expected, atol=2e-4), i


def test_preprocessing_is_the_arrhythmia_heads_recipe() -> None:
    raw = _windows().astype(np.float32)
    got = head.preprocess_windows(raw, SOS)
    assert got.dtype == np.float32
    for i, row in enumerate(raw):
        expected = rc.preprocess(row.tolist(), WEIGHTS)
        assert np.abs(got[i] - expected).max() < 5e-4, i


def test_preprocessing_clips_a_huge_spike_to_the_bound() -> None:
    raw = _windows(1).astype(np.float32)
    raw[0, 1200] += 1e4
    got = head.preprocess_windows(raw, SOS)
    assert got.max() == pytest.approx(head.CLIP)
    assert got.min() >= -head.CLIP


# --- snippets and context -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pos", "expected"),
    [(59, False), (60, True), (1250, True), (2440, True), (2441, False), (0, False), (2499, False)],
)
def test_a_beat_is_callable_only_when_its_whole_snippet_is_inside_the_window(
    pos: int, expected: bool
) -> None:
    assert bool(head.callable_mask(np.array([pos]))[0]) is expected


def test_snippets_are_the_samples_around_the_peak_and_zeros_where_not_callable() -> None:
    norm = np.arange(2 * 2500, dtype=np.float32).reshape(2, 2500)
    snips, ok = head.extract_snippets(norm, np.array([0, 1, 1]), np.array([100, 2440, 10]))
    assert ok.tolist() == [True, True, False]
    assert snips.shape == (3, 120)
    assert snips[0].tolist() == norm[0, 40:160].tolist()
    assert snips[1].tolist() == norm[1, 2380:2500].tolist()
    assert (snips[2] == 0).all()


def test_context_rows_say_what_is_present_and_clip_the_ratios() -> None:
    prev = np.array([0.8, 0.8, NAN, 0.2, 5.0, 0.8])
    nxt = np.array([1.0, NAN, 1.2, 1.0, 1.0, 0.8])
    med = np.array([1.0, 1.0, 1.0, 1.0, 1.0, NAN])
    got = head.context_features(prev, nxt, med)
    assert got.dtype == np.float32
    assert got[0].tolist() == pytest.approx([1.0, 1.0, -0.2, 0.0])
    assert got[1].tolist() == pytest.approx([1.0, 0.0, -0.2, 0.0])  # no interval after
    assert got[2].tolist() == pytest.approx([0.0, 1.0, 0.0, 0.2])  # none before
    assert got[3, 2] == pytest.approx(-0.8)
    assert got[4, 2] == pytest.approx(2.0)  # ratio 5 is clipped to +2
    assert got[5].tolist() == [0.0, 0.0, 0.0, 0.0]  # no median: nothing is known


def test_a_ratio_below_the_clip_is_clipped_at_minus_one() -> None:
    got = head.context_features(np.array([0.0]), np.array([0.0]), np.array([1.0]))
    assert got[0].tolist() == [1.0, 1.0, -1.0, -1.0]


# --- the twelve numbers -------------------------------------------------------------------------


def _pulse(sigma: float, amp: float, centre: int = 60, size: int = 120) -> np.ndarray:
    t = np.arange(size) - centre
    return (amp * np.exp(-0.5 * (t / sigma) ** 2)).astype(np.float32)


def _context(n: int) -> np.ndarray:
    ctx = np.zeros((n, 4), dtype=np.float32)
    ctx[:, 0] = ctx[:, 1] = 1.0
    return ctx


def test_a_beat_identical_to_the_windows_median_beat_has_the_neutral_features() -> None:
    snips = np.stack([_pulse(4, 2.0)] * 5)
    got = head.beat_features(snips, np.ones(5, bool), np.zeros(5, dtype=np.int64), _context(5))
    assert got.shape == (5, 12)
    assert got[0, 4] == 1.0  # has a template
    assert got[0, 5] == pytest.approx(1.0)  # correlation
    assert got[0, 6] == pytest.approx(0.0)  # amplitude ratio
    assert got[0, 7] == pytest.approx(0.0)  # width ratio
    assert got[0, 8] == 1.0  # same polarity
    assert got[0, 10] == pytest.approx(2.0, abs=1e-6)  # peak to peak
    assert got[0, 11] == pytest.approx(2.0, abs=1e-6)  # max |x|
    assert got[0, 9] == pytest.approx(head._width(snips[0]) / 120)


def test_a_wide_tall_inverted_beat_differs_from_the_template_in_the_expected_directions() -> None:
    normal = _pulse(4, 1.0)
    odd = -_pulse(9, 3.0)
    snips = np.stack([normal] * 4 + [odd])
    got = head.beat_features(snips, np.ones(5, bool), np.zeros(5, dtype=np.int64), _context(5))
    assert got[4, 5] < 0.0  # anti-correlated
    assert got[4, 6] > 1.0  # more than twice as tall
    assert got[4, 7] > 0.5  # wider
    assert got[4, 8] == -1.0  # opposite polarity
    assert got[0, 5] == pytest.approx(1.0)  # the normal beats still match the median beat
    assert got[4, 9] > got[0, 9]  # absolute width


def test_a_window_with_fewer_than_four_callable_beats_has_no_template() -> None:
    snips = np.stack([_pulse(4, 1.0)] * 4)
    window = np.zeros(4, dtype=np.int64)
    callable_ = np.array([True, True, True, False])
    got = head.beat_features(snips, callable_, window, _context(4))
    assert got[:, 4].tolist() == [0.0, 0.0, 0.0, 0.0]
    assert got[:3, 5:9].sum() == 0.0
    assert got[0, 9] > 0.0  # the absolute numbers are there
    full = head.beat_features(snips, np.ones(4, bool), window, _context(4))
    assert full[:, 4].tolist() == [1.0, 1.0, 1.0, 1.0]  # four callable beats are enough


def test_each_window_is_compared_with_its_own_median_beat() -> None:
    a = _pulse(4, 1.0)
    b = -_pulse(4, 1.0)
    snips = np.stack([a] * 4 + [b] * 4)
    window = np.array([0] * 4 + [1] * 4)
    got = head.beat_features(snips, np.ones(8, bool), window, _context(8))
    assert got[:, 5] == pytest.approx(1.0)  # each matches its own window's template
    assert (got[:, 8] == 1.0).all()


def test_a_beat_that_is_not_callable_keeps_only_the_interval_features() -> None:
    snips = np.stack([_pulse(4, 1.0)] * 5)
    ok = np.array([True, True, True, True, False])
    ctx = _context(5)
    ctx[4] = [1.0, 0.0, -0.3, 0.0]
    got = head.beat_features(snips, ok, np.zeros(5, dtype=np.int64), ctx)
    assert got[4].tolist() == pytest.approx([1.0, 0.7, 0.0, 1.0] + [0.0] * 8)


def test_the_interval_features_are_the_ratios_with_one_where_absent() -> None:
    snips = np.stack([_pulse(4, 1.0)] * 4)
    ctx = np.array(
        [[1, 1, -0.4, 0.5], [1, 0, -0.1, 0.0], [0, 1, 0.0, 0.2], [0, 0, 0.0, 0.0]], np.float32
    )
    got = head.beat_features(snips, np.ones(4, bool), np.zeros(4, dtype=np.int64), ctx)
    assert got[:, 0].tolist() == [1.0, 1.0, 0.0, 0.0]
    assert got[:, 1].tolist() == pytest.approx([0.6, 0.9, 1.0, 1.0])
    assert got[:, 2].tolist() == [1.0, 0.0, 1.0, 0.0]
    assert got[:, 3].tolist() == pytest.approx([1.5, 1.0, 1.2, 1.0])


def test_a_flat_snippet_and_a_flat_template_do_not_divide_by_zero() -> None:
    snips = np.zeros((4, 120), dtype=np.float32)
    with np.errstate(all="raise"):
        got = head.beat_features(snips, np.ones(4, bool), np.zeros(4, dtype=np.int64), _context(4))
    assert np.isfinite(got).all()
    assert got[0, 5] == 0.0  # no correlation to speak of
    assert got[0, 9] == 0.0


def test_a_flat_beat_among_normal_ones_has_zero_correlation_not_nan() -> None:
    snips = np.stack([_pulse(4, 1.0)] * 4 + [np.zeros(120, dtype=np.float32)])
    with np.errstate(all="raise"):
        got = head.beat_features(snips, np.ones(5, bool), np.zeros(5, dtype=np.int64), _context(5))
    assert np.isfinite(got).all()
    assert got[4, 5] == 0.0
    assert head._corr(np.zeros(5), np.arange(5.0)) == 0.0
    assert head._corr(np.arange(5.0), np.zeros(5)) == 0.0
    assert head._corr(np.arange(5.0), 2 * np.arange(5.0)) == pytest.approx(1.0)


def test_a_ratio_beside_a_zero_flag_is_not_read() -> None:
    snips = np.stack([_pulse(4, 1.0)] * 4)
    ctx = np.array(
        [[0, 1, -0.4, 0.3], [1, 0, 0.5, -0.2], [0, 0, 0.9, 0.9], [1, 1, 0.1, 0.1]], np.float32
    )
    got = head.beat_features(snips, np.ones(4, bool), np.zeros(4, dtype=np.int64), ctx)
    assert got[:, 1].tolist() == pytest.approx([1.0, 1.5, 1.0, 1.1])
    assert got[:, 3].tolist() == pytest.approx([1.3, 1.0, 1.0, 1.1])


def test_peak_to_peak_is_capped_and_width_counts_samples_above_a_quarter_of_the_peak() -> None:
    huge = np.zeros((4, 120), dtype=np.float32)
    huge[:, 55:65] = 100.0
    got = head.beat_features(huge, np.ones(4, bool), np.zeros(4, dtype=np.int64), _context(4))
    assert got[0, 10] == head.PEAK_TO_PEAK_CAP
    assert got[0, 11] == 100.0
    assert got[0, 9] == pytest.approx(10 / 120)
    edge = np.zeros(120, dtype=np.float32)
    edge[60] = 4.0
    edge[61] = 1.0  # exactly a quarter of the peak: counted
    edge[62] = 0.99  # just under: not counted
    assert head._width(edge) == 2.0


# --- the boosted trees --------------------------------------------------------------------------


def _ensemble() -> dict[str, np.ndarray]:
    """Two trees padded to five nodes; baseline -0.5; depth 2.

    tree 0: x[1] <= 0.8 -> +2.0 else -1.0
    tree 1: x[5] <= 0.5 -> (x[0] <= 0.3 -> +0.7 else -0.2) else +0.4"""
    feat = np.full((2, 5), -1, dtype=np.int16)
    thr = np.zeros((2, 5))
    left = np.zeros((2, 5), dtype=np.int16)
    right = np.zeros((2, 5), dtype=np.int16)
    val = np.zeros((2, 5))
    feat[0, 0], thr[0, 0], left[0, 0], right[0, 0] = 1, 0.8, 1, 2
    val[0, 1], val[0, 2] = 2.0, -1.0
    feat[1, 0], thr[1, 0], left[1, 0], right[1, 0] = 5, 0.5, 1, 2
    feat[1, 1], thr[1, 1], left[1, 1], right[1, 1] = (
        0,
        0.3,
        3,
        4,
    )  # feature 0: index zero is a split
    val[1, 2], val[1, 3], val[1, 4] = 0.4, 0.7, -0.2
    return {
        "tree_feature": feat,
        "tree_threshold": thr,
        "tree_left": left,
        "tree_right": right,
        "tree_value": val,
        "baseline": np.array([-0.5]),
        "max_depth": np.array([2], dtype=np.int32),
    }


def _row(**kw: float) -> np.ndarray:
    x = np.zeros(12)
    for k, v in kw.items():
        x[int(k[1:])] = v
    return x


def test_the_forward_pass_follows_every_branch_of_a_hand_built_ensemble() -> None:
    params = _ensemble()
    rows = np.stack(
        [
            _row(f1=0.5, f5=0.2, f0=0.1),  # +2.0 and +0.7
            _row(f1=0.9, f5=0.2, f0=0.1),  # -1.0 and +0.7
            _row(f1=0.5, f5=0.2, f0=0.9),  # +2.0 and -0.2
            _row(f1=0.5, f5=0.9, f0=0.0),  # +2.0 and +0.4
            _row(f1=0.8, f5=0.5, f0=0.3),  # exactly on every threshold: left, left, left
        ]
    )
    raw = head.trees_raw(params, rows)
    assert raw.tolist() == pytest.approx(
        [-0.5 + 2.0 + 0.7, -0.5 - 1.0 + 0.7, -0.5 + 2.0 - 0.2, -0.5 + 2.0 + 0.4, -0.5 + 2.0 + 0.7]
    )
    assert head.predict_proba(params, rows).tolist() == pytest.approx(
        (1 / (1 + np.exp(-raw))).tolist()
    )


def test_the_node_count_is_what_the_roots_reach_not_the_padding() -> None:
    assert head.n_nodes(_ensemble()) == 3 + 5


def test_the_logistic_does_not_overflow_and_is_symmetric() -> None:
    with np.errstate(over="raise", invalid="raise", divide="raise"):  # underflow to 0 is fine
        got = head.sigmoid(np.array([-1000.0, -2.0, 0.0, 2.0, 1000.0]))
    assert got[0] == 0.0
    assert got[4] == 1.0
    assert got[2] == 0.5
    assert got[1] + got[3] == pytest.approx(1.0)


def _fake_model() -> SimpleNamespace:
    dtype = [
        ("value", "f8"),
        ("feature_idx", "u4"),
        ("num_threshold", "f8"),
        ("left", "u4"),
        ("right", "u4"),
        ("is_leaf", "u1"),
    ]
    big = np.zeros(3, dtype=dtype)
    big["feature_idx"] = [4, 0, 0]
    big["num_threshold"] = [0.25, 0.0, 0.0]
    big["left"] = [1, 0, 0]
    big["right"] = [2, 0, 0]
    big["value"] = [9.9, 0.3, -0.1]  # the root's value is not a prediction
    big["is_leaf"] = [0, 1, 1]
    small = np.zeros(1, dtype=dtype)
    small["value"] = [0.05]
    small["is_leaf"] = [1]
    return SimpleNamespace(
        _predictors=[[SimpleNamespace(nodes=big)], [SimpleNamespace(nodes=small)]],
        _baseline_prediction=np.array([[-1.5]]),
    )


def test_fitted_trees_are_exported_padded_with_leaf_values_only_at_leaves() -> None:
    got = head.export_trees(_fake_model())
    assert got["tree_feature"].tolist() == [[4, -1, -1], [-1, -1, -1]]
    assert got["tree_threshold"][0, 0] == 0.25
    assert got["tree_left"].tolist() == [[1, 0, 0], [0, 0, 0]]
    assert got["tree_right"].tolist() == [[2, 0, 0], [0, 0, 0]]
    assert got["tree_value"].tolist() == [[0.0, 0.3, -0.1], [0.05, 0.0, 0.0]]
    assert got["baseline"].tolist() == [-1.5]
    assert int(got["max_depth"][0]) == head.GBM["max_depth"]
    x = np.zeros((2, 12))
    x[1, 4] = 0.9
    params = got | {"max_depth": np.array([1], dtype=np.int32)}
    assert head.trees_raw(params, x).tolist() == pytest.approx(
        [-1.5 + 0.3 + 0.05, -1.5 - 0.1 + 0.05]
    )


# --- one record ---------------------------------------------------------------------------------


def _beats(callable_: list[bool]) -> object:
    n = len(callable_)
    rng = np.random.default_rng(3)
    feats = rng.normal(size=(n, 12))
    feats[:, 1] = rng.uniform(0.4, 1.2, n)
    return head.RecordBeats(None, feats, np.array(callable_), np.zeros(n, dtype=bool))


def test_a_beat_that_is_not_callable_has_no_probability_and_is_never_called() -> None:
    params = _ensemble() | {"threshold": np.array([0.0], dtype=np.float32)}
    beats = _beats([True, False, True, False])
    prob, called = head.call_record(params, beats)
    assert np.isnan(prob[[1, 3]]).all()
    assert np.isfinite(prob[[0, 2]]).all()
    assert called.tolist() == [True, False, True, False]  # threshold 0: every callable beat


def test_the_threshold_is_inclusive_and_can_be_overridden() -> None:
    params = _ensemble() | {"threshold": np.array([0.5], dtype=np.float32)}
    beats = _beats([True, True, True])
    prob, _ = head.call_record(params, beats)
    exact = float(prob[1])
    _, called = head.call_record(params, beats, threshold=exact)
    assert called[1]
    _, called = head.call_record(params, beats, threshold=float(np.nextafter(exact, 1.0)))
    assert not called[1]


def test_a_record_without_detections_is_empty_not_an_error() -> None:
    params = _ensemble() | {"threshold": np.array([0.5], dtype=np.float32)}
    beats = head.RecordBeats(
        None, np.zeros((0, 12)), np.zeros(0, dtype=bool), np.zeros(0, dtype=bool)
    )
    prob, called = head.call_record(params, beats)
    assert prob.size == 0
    assert called.size == 0


def test_the_first_annotated_beat_is_labelled_like_any_other() -> None:
    x, pos, sym = ecg_with_premature_beats((15,))
    relabelled = sym.copy()
    relabelled[0] = "V"  # the first reference beat, matched to the first detection
    beats = head.record_beats(x, pos, relabelled, SOS)
    assert beats.label[0]
    assert beats.label.sum() == 2


def test_the_loader_reads_arrays_and_refuses_pickled_objects(tmp_path: Path) -> None:
    good = tmp_path / "good.npz"
    np.savez(good, a=np.arange(3.0), b=np.array([1], dtype=np.int32))
    got = head.load_params(good)
    assert set(got) == {"a", "b"}
    assert got["a"].tolist() == [0.0, 1.0, 2.0]
    bad = tmp_path / "bad.npz"
    np.savez(bad, a=np.array([{"x": 1}], dtype=object))
    with pytest.raises(ValueError, match="pickle"):
        head.load_params(bad)


def test_the_threshold_helper_calls_only_callable_beats_at_or_above_it() -> None:
    prob = np.array([0.1, 0.4, 0.4, 0.9, np.nan])
    ok = np.array([True, True, False, True, False])
    assert head.apply_threshold(prob, ok, 0.4).tolist() == [False, True, False, True, False]
    assert head.apply_threshold(prob, ok, 0.95).tolist() == [False] * 5


def test_the_end_to_end_record_path_labels_and_marks_snippets_on_synthetic_ecg() -> None:
    x, pos, sym = ecg_with_premature_beats((15, 40, 65, 95))
    beats = head.record_beats(x, pos, sym, SOS)
    assert beats.label.sum() == 4  # every premature beat was detected and matched to a V
    assert beats.callable[beats.label].all()  # none of them sits at a window edge
    assert beats.features.shape == (len(beats.callable), 12)
    assert (beats.features[beats.callable, 4] == 1.0).mean() > 0.9  # most have a median beat
    # a premature beat has a short interval before it and none of the others does
    ratio = beats.features[:, 1]
    has_prev = beats.features[:, 0] == 1.0
    assert (ratio[beats.label] < 0.8).all()
    assert (ratio[has_prev & ~beats.label] > 0.85).sum() > 100
    # and it is the odd one out against its window's median beat
    corr = beats.features[:, 5]
    assert corr[beats.label].mean() < corr[beats.callable & ~beats.label].mean()


# --- folds --------------------------------------------------------------------------------------


def test_the_folds_are_record_grouped_exhaustive_deterministic_and_balanced() -> None:
    counts = {name: i * 7 % 50 for i, name in enumerate(rb.DS1)}
    folds = head.make_folds(counts)
    flat = [n for f in folds for n in f]
    assert sorted(flat) == sorted(rb.DS1)  # every record exactly once: none in two folds
    assert len(set(flat)) == len(flat)
    assert head.make_folds(counts) == folds
    assert len(folds) == head.FOLDS
    totals = [sum(counts[n] for n in f) for f in folds]
    assert max(totals) - min(totals) <= max(counts.values())
    assert all(sizes in (4, 5) for sizes in (len(f) for f in folds))
    assert all(f == sorted(f) for f in folds)


def test_the_fold_assignment_is_the_documented_snake_by_descending_v_count() -> None:
    counts = dict(zip("abcdefghijkl", [100, 90, 80, 70, 60, 50, 40, 30, 20, 10, 5, 1], strict=True))
    assert head.make_folds(counts, 5) == [
        ["a", "j", "k"],
        ["b", "i", "l"],
        ["c", "h"],
        ["d", "g"],
        ["e", "f"],
    ]


def test_the_fold_assignment_deals_the_largest_records_to_different_folds() -> None:
    counts = {"a": 50, "b": 40, "c": 30, "d": 20, "e": 10, "f": 9, "g": 8, "h": 7, "i": 6, "j": 5}
    folds = head.make_folds(counts, 5)
    heads = [f for f in folds if set(f) & {"a", "b", "c", "d", "e"}]
    assert len(heads) == 5  # the five largest are in five different folds
    assert folds[0] == ["a", "j"]  # snake: the first fold gets the largest and the smallest


# --- what is frozen -----------------------------------------------------------------------------


def test_the_head_is_configured_as_the_pin_will_record_it() -> None:
    assert head.GBM == {
        "max_depth": 3,
        "max_iter": 60,
        "learning_rate": 0.1,
        "min_samples_leaf": 50,
        "l2_regularization": 1.0,
        "early_stopping": False,  # its validation split would be a random beat-level one
        "random_state": 0,
    }
    assert head.FEATURE_NAMES[:5] == (
        "has_prev",
        "ratio_prev",
        "has_next",
        "ratio_next",
        "has_template",
    )
    assert head.N_FEATURES == 12
    assert (head.HALF, head.SNIPPET, head.MIN_TEMPLATE_BEATS) == (60, 120, 4)
    assert head.FOLDS == 5
    assert head.SEED == 20261014
    assert head.THRESHOLDS[0] == 0.05
    assert head.THRESHOLDS[-1] == 0.95
    assert len(head.THRESHOLDS) == 19
    assert (head.CLIP, head.MAD_SCALE, head.EPS) == (10.0, 1.4826, 1e-6)
    assert (head.WIDTH_FRACTION, head.PEAK_TO_PEAK_CAP) == (0.25, 40.0)


def test_the_golden_inputs_are_seeded_synthetic_and_cover_missing_context() -> None:
    snips, ctx, window = head.golden_inputs()
    again, ctx_again, _ = head.golden_inputs()
    assert snips.shape == (8, 120)
    assert snips.dtype == np.float32
    assert np.array_equal(snips, again)
    assert np.array_equal(ctx, ctx_again)
    assert window.tolist() == [0] * 8  # one window: they share a median beat
    assert ctx[6].tolist() == [0.0, 0.0, 0.0, 0.0]
    assert ctx[7, 1] == 0.0
    assert ctx[0, 0] == 1.0
