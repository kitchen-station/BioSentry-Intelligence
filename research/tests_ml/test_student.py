import builtins
import inspect
import json
import os
import re
import shutil
import types
from dataclasses import replace
from pathlib import Path

import mindspore as ms
import numpy as np
import onnx
import pytest
from mindspore import Tensor, nn

from cohort import stay, trial, vitals_row
from research import bootstrap, evaluate, evaluate_student, student, train_student, trials
from research.alerts import POLICIES
from research.bootstrap import SplitMix64
from research.student import (
    Grid, Student, chart_burden, feature_names, load_for_training, match_threshold, model_burden,
    paired_gate, patient_features, patient_labels, prob_policy, stay_features, to_onnx, trial_features, view,
)
from research.trials import Episode, TrialError, load_trial

TOY = Path(__file__).parent.parent / "tests" / "data" / "toy"
ALLOWED_OPS = {"Sub", "Div", "Gemm", "Relu", "Sigmoid"}  # exactly the ops the graph uses
SHA = "95052d021b73da3fa71cc62601a795264b8ffd1cf5c4bf2e6e3ba407f74698a8"
IDX = student._INTERNAL.index


# small enough for the gate, large enough that every code path runs: 2 teacher fits, 4 + 4 student fits
TINY = Grid(teacher_widths=(64, 64), teacher_decays=(1e-5, 1e-4), teacher_epochs=2, student_widths=(4, 8),
            student_epochs=2, hgb_b_iter=20, hgb_d_iter=20, hgb_d_min_leaf=20, parity_rows=100)


@pytest.fixture(scope="module")
def tfs():
    return [trial_features(trial(s)) for s in (61, 62, 261)]


@pytest.fixture(scope="module")
def built(tfs):
    return student.build(tfs[:2], tfs[2:], TINY)


def ext_folder(root: Path, name: str, seed: int, **manifest) -> Path:
    """A loadable folder (the toy trial) with a manifest that says it is seed `seed` of ward-v2-ext."""
    folder = shutil.copytree(TOY, root / name)
    m = json.loads((folder / "manifest.json").read_text())
    m.update({"seed": seed, "days": 30, "speed": 60, "scenario_sha256": SHA} | manifest)
    (folder / "manifest.json").write_text(json.dumps(m))
    return folder


@pytest.fixture
def opened(monkeypatch):
    """Every file opened (os.open and open) under the test's data root, by (folder, name)."""
    log: list[tuple[str, str]] = []
    real_os, real_open = os.open, builtins.open

    def note(path):
        p = Path(str(path))
        if p.name != "" and p.parent.parent == note.root:
            log.append((p.parent.name, p.name))

    note.root = None
    monkeypatch.setattr(os, "open", lambda path, *a, **k: (note(path), real_os(path, *a, **k))[1])
    monkeypatch.setattr(builtins, "open", lambda path, *a, **k: (note(path), real_open(path, *a, **k))[1])
    return log, note


# --- the feature sets ---

def test_feature_names_and_counts():
    assert [len(feature_names(t, k)) for k in ("base", "trend", "teacher") for t in (True, False)] == \
        [19, 15, 35, 27, 53, 41]
    for kind in ("base", "trend", "teacher"):
        assert not any(n.startswith("temperature") for n in feature_names(False, kind))
        assert len(set(feature_names(True, kind))) == len(feature_names(True, kind))
    names = " ".join(feature_names(True, "teacher"))
    assert "time" not in names and "patient" not in names and "seed" not in names and "outcome" not in names
    assert len(student._INTERNAL) == 56


def _ramp_stay():
    """Heart rate 60 + 2 per bin (8 a hour), everything else normal; 40 bins."""
    return {15 * b: vitals_row("p-9", 15 * b, 60 + 2 * b) for b in range(1, 41)}


def test_trend_and_window_features_by_hand():
    rows = _ramp_stay()
    minutes, x = stay_features(rows)
    assert x.shape == (40, 56) and np.array_equal(x[:, :20], patient_features(rows)[1])
    col = lambda n: x[:, IDX(n)]  # noqa: E731
    d1, d1m, d4, d4m = (col(f"heart_rate_{k}") for k in ("d1h", "d1h_missing", "d4h", "d4h_missing"))
    assert d1m.tolist() == [1.0] * 4 + [0.0] * 36 and d1[:4].tolist() == [0.0] * 4
    assert np.allclose(d1[4:], 8.0) and d4m.tolist() == [1.0] * 16 + [0.0] * 24 and np.allclose(d4[16:], 32.0)
    mean, mean_m, slope, slope_m = (col(f"heart_rate_{k}") for k in ("mean4h", "mean4h_missing", "slope4h",
                                                                    "slope4h_missing"))
    assert slope_m.tolist() == [1.0] * 3 + [0.0] * 37 and np.allclose(slope[3:], 8.0)  # needs 4 valid points
    assert mean_m.sum() == 0 and np.isclose(mean[2], 60 + 2 * 2) and np.isclose(mean[39], 60 + 2 * 32.5)
    # NEWS2 change over one hour is the difference of the sim's totals, with its flag
    n2 = np.array([rows[m].news2 for m in minutes], dtype=float)
    assert np.array_equal(col("news2_d1h")[4:], n2[4:] - n2[:-4]) and col("news2_d1h_missing")[:4].tolist() == [1.0] * 4
    # temperature never moves here, so the version without its points changes the same way
    assert np.array_equal(col("news2_d1h")[4:], col("news2_d1h_without_temperature_points")[4:])


def test_a_lag_bin_that_is_not_ranked_still_gives_its_values():
    rows = _ramp_stay()
    rows[15 * 10] = vitals_row("p-9", 150, 60 + 2 * 10, spo2=None)  # NOT RANKED, heart rate still there
    minutes, x = stay_features(rows)
    assert 150 not in minutes
    i = minutes.index(15 * 14)  # one hour later: its lag bin is the NOT RANKED one
    assert x[i, IDX("heart_rate_d1h_missing")] == 0.0 and x[i, IDX("heart_rate_d1h")] == 8.0
    assert x[i, IDX("spo2_d1h_missing")] == 1.0 and x[i, IDX("spo2_d1h")] == 0.0  # its SpO2 is missing
    assert x[i, IDX("news2_d1h_missing")] == 1.0  # and it has no NEWS2 total


# --- causality ---

def _edits(rows, m):
    return [
        {**rows, m: vitals_row("p-000", m, 150, 30, 85, 39.5)},  # a later bin changed
        {mm: r for mm, r in rows.items() if mm != m},  # a later bin removed
        {**rows, m: replace(rows[m], ranked=False, news2=None)},  # a later bin turned NOT RANKED
        {mm: r for mm, r in rows.items() if mm <= m},  # the stay cut after the bin
    ]


def test_changing_a_later_bin_changes_no_earlier_feature_row():
    rows, _ = stay("p-000", 0, 1, episode=True)
    minutes, x = stay_features(rows)
    for k in (10, 40, 90):  # before the baseline forms, just after, and late
        m = minutes[k]
        for edited in _edits(rows, m):
            em, ex = stay_features(edited)
            assert em[:k] == minutes[:k]
            assert np.array_equal(ex[:k], x[:k])  # bit-identical, trend and teacher columns included


def test_a_stay_cut_at_a_bin_equals_that_row_of_the_full_stay():
    rows, _ = stay("p-000", 0, 1, episode=True)
    minutes, x = stay_features(rows)
    for k in (5, 30, 100):
        cm, cx = stay_features({m: r for m, r in rows.items() if m <= minutes[k]})
        assert cm == minutes[:k + 1] and np.array_equal(cx, x[:k + 1])


def test_features_are_label_blind(tfs):
    t = trial(61)
    other = replace(t, episodes=tuple(replace(e, kind="tachycardic", start=100.5, ramp_minutes=30.0)
                                      for e in t.episodes))
    a = trial_features(other)
    for name in student._ARRAYS:
        if name != "y":
            assert np.array_equal(getattr(a, name), getattr(tfs[0], name)), name
    assert not np.array_equal(a.y, tfs[0].y)


def test_outcome_in_episodes_csv_changes_no_feature(tmp_path):
    a, b = ext_folder(tmp_path, "a", 61), ext_folder(tmp_path, "b", 61)
    text = (b / "episodes.csv").read_text().replace("discharged", "higher_care")
    (b / "episodes.csv").write_text(text)
    assert text != (a / "episodes.csv").read_text()
    fa, fb = (student.extract(str(f)) for f in (a, b))
    assert all(np.array_equal(getattr(fa, n), getattr(fb, n)) for n in student._ARRAYS)


# --- labels and rows ---

def test_label_windows_start_to_fully_in_positive_after_excluded():
    e = Episode("p", 15, None, "respiratory", 100.5, 30.0)  # fully in at 130.5
    assert patient_labels([90, 105, 120, 135, 600], e).tolist() == [0, 1, 1, -1, -1]
    assert patient_labels([105, 120], Episode("p", 15, None, None, None, None)).tolist() == [0, 0]
    late = Episode("p", 15, 200, "respiratory", 900.0, 30.0)  # starts after the stay
    assert patient_labels([15, 30, 195], late).tolist() == [0, 0, 0]


def test_starting_patients_and_excluded_bins_are_not_training_rows():
    t = trial(61, patients=2)
    tf = trial_features(t)
    x, y, g = student._stack([tf], "teacher", True)
    _, e = stay("p-000", 0, 61, episode=True)
    rows = {r.minute: r for r in t.rows if r.patient == "p-000"}
    labels = patient_labels(patient_features(rows)[0], e)
    assert len(y) == (labels >= 0).sum() + 120 and set(y.tolist()) == {0, 1}
    assert x.dtype == np.float32 and x.shape[1] == 53 and set(g.tolist()) == {0, 1}
    starting = trial_features(replace(t, episodes=tuple(replace(ep, admit_minute=0) for ep in t.episodes)))
    assert len(student._stack([starting], "base", True)[1]) == 0
    assert len(starting.x) == len(tf.x)  # their bins are still scored for alert burden


def test_missing_is_zero_plus_flag_and_never_filled():
    rows, _ = stay("p-001", 1, 1, episode=False)
    names = feature_names(True)
    minutes, x = patient_features(rows)
    z, zm = names.index("heart_rate_z"), names.index("heart_rate_z_missing")
    assert x[:23, zm].tolist() == [1.0] * 23 and x[:23, z].tolist() == [0.0] * 23  # forming
    assert x[23:, zm].tolist() == [0.0] * (len(minutes) - 23)
    # a NOT RANKED bin gets no row; its missing SpO2 is a gap in the baseline, not a value
    gap = {**rows, 15 * 5: vitals_row("p-001", 75, 70, 16, None)}
    gm, gx = patient_features(gap)
    assert 75 not in gm and len(gm) == len(minutes) - 1
    sm = names.index("spo2_z_missing")
    assert gx[22, sm] == 1.0 and gx[23, sm] == 0.0  # SpO2 needs one more bin to form
    assert gx[22, names.index("heart_rate_z_missing")] == 0.0  # heart rate formed on time
    assert not np.isnan(gx).any()
    assert not np.isnan(stay_features(gap)[1]).any()  # trend and window inputs are never NaN either
    # a RANKED row with a missing value cannot happen; if it did, refuse rather than fill
    bad = {**rows, 90: replace(vitals_row("p-001", 90, 70), temperature=None, ranked=True)}
    with pytest.raises(TrialError, match="ranked but NEWS2 abstains"):
        patient_features(bad)


def test_policy_does_not_alert_on_not_ranked_or_missing_bins():
    rows, _ = stay("p-000", 0, 1, episode=True)
    rows = {**rows, 300: vitals_row("p-000", 300, 70, 16, None)}
    pol = prob_policy("t", lambda r: {m: 1.0 for m in r if r[m].ranked}, 0.5)
    states = pol.states(rows)
    assert set(states) == set(rows) and states[300] is False and states[315] is True


# --- no temperature ---

def test_no_temperature_models_have_no_temperature_input(tfs):
    for kind, n in (("base", 15), ("trend", 27), ("teacher", 41)):
        assert len(feature_names(False, kind)) == n
    rows, _ = stay("p-000", 0, 1, episode=True)
    hot = {m: vitals_row("p-000", m, r.heart_rate, r.respiratory_rate, r.spo2, r.temperature + 2.5)
           for m, r in rows.items()}  # 39.3 C and up: NEWS2 temperature points change
    (_, a), (_, b) = stay_features(rows), stay_features(hot)
    for kind in ("base", "trend", "teacher"):
        assert not np.array_equal(view(a, True, kind), view(b, True, kind))
        assert np.array_equal(view(a, False, kind), view(b, False, kind))


# --- the split, enforced where data is read ---

def test_calibration_and_ward_v2_folders_are_skipped_after_reading_only_the_manifest(tmp_path, opened):
    log, note = opened
    note.root = tmp_path
    folders = [ext_folder(tmp_path, "c", 305), ext_folder(tmp_path, "c2", 340), ext_folder(tmp_path, "w", 11),
               ext_folder(tmp_path, "w1", 3), ext_folder(tmp_path, "w2", 60),
               ext_folder(tmp_path, "t", 61), ext_folder(tmp_path, "v", 261)]
    log.clear()  # building the folders copied files
    train, val, skipped = load_for_training([str(f) for f in folders], workers=1)
    assert [t.seed for t in train] == [61] and [t.seed for t in val] == [261]
    assert skipped == {"ward_v2": [3, 11, 60], "calibration": [305, 340]}
    by_folder = {}
    for folder, name in log:
        by_folder.setdefault(folder, set()).add(name)
    for skipped_folder in ("c", "c2", "w", "w1", "w2"):
        assert by_folder[skipped_folder] == {"manifest.json"}
    assert by_folder["t"] == set(trials.FILES)


def test_out_of_range_seeds_and_a_different_scenario_are_refused_before_data_is_read(tmp_path, opened):
    log, note = opened
    note.root = tmp_path
    bad_seed = [ext_folder(tmp_path, f"s{seed}", seed) for seed in (161, 200, 341, 400, 700)]
    bad_shape = [ext_folder(tmp_path, "x" + next(iter(c)), 62, **c)
                 for c in ({"days": 29}, {"speed": 10}, {"scenario_sha256": "0" * 64})]
    twice = [ext_folder(tmp_path, "d1", 63), ext_folder(tmp_path, "d2", 63)]
    log.clear()  # building the folders copied files
    for f in bad_seed:
        with pytest.raises(TrialError, match="is not used here"):
            load_for_training([str(f)], workers=1)
    for f in bad_shape:
        with pytest.raises(TrialError, match="differ from ward-v2's"):
            load_for_training([str(f)], workers=1)
    with pytest.raises(TrialError, match="given twice"):
        load_for_training([str(f) for f in twice], workers=1)
    assert {name for _, name in log} == {"manifest.json"}  # nothing else was opened


def test_train_command_skips_a_ward_v2_folder_and_writes_nothing(tmp_path):
    held = ext_folder(tmp_path, "held", 11)
    assert train_student.main([str(held), "--out", str(tmp_path / "o")]) == 2
    assert not (tmp_path / "o").exists()


def test_evaluate_refuses_ward_v2_seeds_1_to_10_and_opens_no_ext_seed_above_300(tmp_path, opened):
    log, note = opened
    note.root = tmp_path
    old = [ext_folder(tmp_path, f"w{seed}", seed) for seed in (1, 10)]
    skipped = [ext_folder(tmp_path, f"e{s}", s) for s in (305, 265, 40)]
    other = ext_folder(tmp_path, "e200", 200)
    log.clear()  # building the folders copied files
    for f in old:
        with pytest.raises(TrialError, match="seeds 1 to 10 are no longer used"):
            evaluate_student.run([str(f)], out=tmp_path)
    with pytest.raises(TrialError, match="no ward-v2 folder"):  # 305, 265, 40 are skipped by manifest
        evaluate_student.run([str(f) for f in skipped], out=tmp_path)
    with pytest.raises(TrialError, match="is not used here"):
        evaluate_student.run([str(other)], out=tmp_path)
    assert {name for _, name in log} == {"manifest.json"}
    assert train_student.main([str(tmp_path / "e305")]) == 2  # and the training command needs real seeds


# --- cache and parallel extraction ---

def test_cache_is_keyed_by_the_files_and_holds_the_same_numbers(tmp_path):
    f, cache = ext_folder(tmp_path, "t", 61), tmp_path / "cache"
    first = student.extract(str(f), str(cache))
    assert len(list(cache.glob("*.npz"))) == 1
    again = student.extract(str(f), str(cache))
    assert all(np.array_equal(getattr(first, n), getattr(again, n)) for n in student._ARRAYS)
    assert (first.seed, first.n_stays) == (again.seed, again.n_stays)
    key = student.cache_key(f)
    (f / "cohort.csv").write_text((f / "cohort.csv").read_text().replace(",36.8,", ",36.9,", 1))
    assert student.cache_key(f) != key
    student.extract(str(f), str(cache))
    assert len(list(cache.glob("*.npz"))) == 2
    (cache / f"{student.cache_key(f)}.npz").write_bytes(b"damaged")  # a bad entry is a miss, not an error
    assert student.extract(str(f), str(cache)).seed == 61


def test_parallel_extraction_equals_serial_in_the_same_order(tmp_path):
    folders = [str(ext_folder(tmp_path, f"t{s}", s)) for s in (62, 61)]
    serial = student.extract_all(folders, None, 1)
    parallel = student.extract_all(folders, None, 2)
    assert [t.seed for t in parallel] == [62, 61]
    for a, b in zip(serial, parallel):
        assert all(np.array_equal(getattr(a, n), getattr(b, n)) for n in student._ARRAYS)


def test_standardisation_uses_the_training_seeds_only(tfs):
    xa, _, _ = student._stack(tfs[:2], "base", True)
    mean, std = student._moments(xa)
    assert np.allclose(mean, xa.mean(axis=0, dtype=np.float64), rtol=1e-6, atol=1e-6)
    ref = xa.astype(np.float64).std(axis=0)
    ref[ref <= 1e-9 * np.maximum(1.0, np.abs(xa.mean(axis=0)))] = 1.0
    assert np.allclose(std, ref, rtol=1e-5)
    xb, _, _ = student._stack(tfs, "base", True)  # with the validation seed it would differ
    assert not np.array_equal(student._moments(xb)[0], mean)
    z = student._standardise(xa[:5].copy(), mean, std)
    assert np.array_equal(z, (xa[:5] - mean) / std)  # the graph's own float32 arithmetic


# --- the teacher ---

def _noisy():
    n = 600
    a = np.arange(n, dtype=np.float64)
    x = np.column_stack([np.sin(a * 0.37), np.cos(a * 1.31), (a % 7) / 7]).astype(np.float32)
    y = ((x[:, 0] + 0.8 * np.sin(a * 12.9) > 0.3)).astype(np.int8)
    return x[:400], y[:400], x[400:], y[400:]


# --- gates, tie-breaks and what ships ---

def test_gate_bootstrap_on_hand_made_numbers():
    loss = np.array([0.1, 0.2, 0.3, 0.1, 0.1, 0.1, 0.4, 0.2])
    ref = np.array([0.2, 0.4, 0.2, 0.3, 0.3, 0.3, 0.5, 0.5])
    groups = np.array([7, 7, 3, 9, 9, 9, 1, 1])  # four patients
    got = paired_gate(loss, {"r": ref}, groups, resamples=200)["r"]
    order = {g: i for i, g in enumerate(sorted(set(groups.tolist())))}
    d, n = np.zeros(4), np.zeros(4)
    for g, diff in zip(groups.tolist(), (loss - ref).tolist()):
        d[order[g]] += diff
        n[order[g]] += 1
    rng, vals = SplitMix64(20261112), []
    for _ in range(200):
        idx = [rng.next() % 4 for _ in range(4)]
        vals.append(sum(d[i] for i in idx) / sum(n[i] for i in idx))
    vals.sort()
    assert got["difference"] == round(float((loss - ref).mean()), 6)
    lo, hi = vals[int(0.025 * 200)], vals[int(0.975 * 200) - 1]
    assert (got["lower_2_5"], got["upper_97_5"]) == (round(lo, 6), round(hi, 6))
    assert got["passes"] == (hi < 0)
    worse = paired_gate(loss + 1, {"r": ref}, groups, resamples=200)["r"]
    assert worse["passes"] is False and worse["lower_2_5"] > 0
    assert paired_gate(loss - 1, {"r": ref}, groups, resamples=200)["r"]["passes"] is True


def test_threshold_rule_needs_both_alerts_and_time_in_alert_within_news2s():
    chart = (4, 100)
    assert match_threshold({0.1: (5, 50), 0.2: (3, 80), 0.3: (4, 90)}, chart) == 0.2  # smallest that fits
    assert match_threshold({0.1: (4, 100), 0.2: (3, 80)}, chart) == 0.1  # equal burden is allowed
    assert match_threshold({0.1: (2, 101), 0.2: (9, 5)}, chart) is None  # one alert open all the time
    assert match_threshold({0.1: (3, 101), 0.2: (3, 300), 0.3: (5, 10)}, chart) is None
    assert match_threshold({0.1: (9, 100), 0.2: (3, 99)}, chart) == 0.2


# --- ONNX ---

def _student(n: int = 5, hidden: int = 3) -> Student:
    f = np.float32
    w1 = (np.arange(n * hidden, dtype=f).reshape(n, hidden) % 7 - 3) / f(4)
    return Student("mlp", np.arange(n, dtype=f), np.arange(1, n + 1, dtype=f),
                   ((w1, np.linspace(-1, 1, hidden, dtype=f)), (np.ones((hidden, 1), f) / f(2), np.array([-0.5], f))))


def test_onnx_checker_ops_shape_and_parity_on_1000_rows():
    s = _student()
    data = to_onnx(s)
    model = onnx.load_from_string(data)
    onnx.checker.check_model(model, full_check=True)
    assert model.opset_import[0].version == 13 and model.ir_version == 7
    assert set(student.onnx_ops(data)) == ALLOWED_OPS
    inp, outp = model.graph.input[0].type.tensor_type, model.graph.output[0].type.tensor_type
    assert inp.elem_type == onnx.TensorProto.FLOAT and [d.dim_value for d in inp.shape.dim] == [1, 5]
    assert outp.elem_type == onnx.TensorProto.FLOAT and [d.dim_value for d in outp.shape.dim] == [1, 1]
    x = np.vstack([
        np.sin(np.arange(5000, dtype=np.float64)).reshape(1000, 5) * 40,
        (np.arange(5000).reshape(1000, 5) * 7 % 3 == 0).astype(np.float64),  # 0/1 flags
        np.cos(np.arange(5000, dtype=np.float64)).reshape(1000, 5) * 1e4,  # extremes near +-1e4
    ])
    got = student.onnx_predict(student.session(data), x)
    assert got.dtype == np.float32 and got.min() >= 0 and got.max() <= 1
    assert np.max(np.abs(got - s.forward(x))) <= 1e-5
    assert s.parameters == 5 * 3 + 3 + 3 + 1


# --- alert counts ---

def test_alert_counts_by_array_equal_the_policy_path():
    t = trial(61)
    tf = trial_features(t)
    fn = lambda v: 1 / (1 + np.exp(-(v[:, 0].astype(np.float64) - 95) / 4))  # noqa: E731
    p = fn(view(tf.x, True, "base"))
    burden = model_burden([tf], [p])
    pol = [prob_policy(f"t{th}", student.Scorer(fn, True), th) for th in (0.01, 0.1, 0.5, 0.9)]
    pool = evaluate.Pool()
    evaluate.score_trial(t, (POLICIES[0], *pol), pool)
    for th in (0.01, 0.1, 0.5, 0.9):
        assert (pool.tallies[f"t{th}"].alerts, pool.tallies[f"t{th}"].on_bins) == burden[th]
    chart, bins, days = chart_burden([tf])
    assert chart == (pool.tallies[POLICIES[0].name].alerts, pool.tallies[POLICIES[0].name].on_bins)
    assert bins == len(t.rows) and days == t.patient_days
    assert any(a > 0 for a, _ in burden.values())
    student.Scorer.clear()


def test_alert_counts_follow_the_alert_rule(tfs):
    constant = lambda tf, p: np.full(len(tf.x), p)  # noqa: E731
    burden = model_burden(tfs[:1], [constant(tfs[0], 0.5)])
    assert all(a == 8 for th, (a, _) in burden.items() if th <= 0.5)  # alerting from the first bin: one each
    assert all(on == len(tfs[0].x) for th, (_, on) in burden.items() if th <= 0.5)
    assert all(v == (0, 0) for th, v in burden.items() if th > 0.5)


# --- the MindSpore networks ---

def test_teacher_and_student_probabilities_are_causal(built):
    rows, _ = stay("p-000", 0, 1, episode=True)
    n = len(feature_names(True, "trend"))
    cells = {"teacher": student.Cell(built.results["all"].teacher), "trend": student.Cell(_student(n, 3))}
    for kind, cell in cells.items():
        predict = lambda x, c=cell: student.forward(c, x)  # noqa: E731
        full = student.Scorer(predict, True, kind)(rows)
        assert len(full) > 100
        m = 15 * 60
        for edited in _edits(rows, m):
            got = student.Scorer(predict, True, kind)(edited)
            assert {k: v for k, v in got.items() if k < m} == {k: v for k, v in full.items() if k < m}
        student.Scorer.clear()


def test_build_refuses_trials_outside_the_split(tfs):
    for bad in (11, 1, 161, 300):
        with pytest.raises(TrialError, match="not in seeds 61 to 160"):
            student.build([replace(tfs[0], seed=bad)], tfs[2:], TINY)
    for bad in (160, 301, 362):
        with pytest.raises(TrialError, match="not in seeds 261 to 300"):
            student.build(tfs[:1], [replace(tfs[2], seed=bad)], TINY)


def _fit_kw(**kw):
    return {"widths": (4,), "loss": "bce", "decay": 1e-4, "batch": 64, "patience": 1, "bias": -1.0, "lr": 1e-2} | kw


def test_early_stopping_keeps_the_best_epoch_and_a_tie_keeps_the_earlier_one():
    def run(losses, patience):
        stop, new = student.Stopper(patience), []
        for v in losses:
            new.append(stop.update(v))
            if stop.done:
                break
        return stop.best_epoch, new

    assert run([0.5, 0.4, 0.45, 0.41, 0.3], 2) == (2, [True, True, False, False])  # stops before 0.3
    assert run([0.5, 0.4, 0.4, 0.4, 0.4], 3) == (2, [True, True, False, False, False])  # ties: epoch 2
    assert run([0.5, 0.4, 0.3, 0.2], 2) == (4, [True] * 4)
    assert run([float("nan"), 0.4], 3)[0] == 2  # a NaN epoch is never best
    x, y, xv, yv = _noisy()
    f = student.fit_net(x, y, epochs=8, val=(xv, yv), **_fit_kw())
    assert 1 <= f.best_epoch <= len(f.val_losses) <= 8 and f.val_losses[f.best_epoch - 1] == min(f.val_losses)
    kept = student.Cell(Student("m", np.zeros(3, np.float32), np.ones(3, np.float32), f.layers), sigmoid=False)
    assert abs(student.log_loss(yv, student._sigmoid(student.forward(kept, xv))) - min(f.val_losses)) < 1e-9


def test_a_refit_for_the_best_epoch_count_reproduces_the_monitored_weights():
    # the evaluator relies on this: weights after epoch k of a monitored fit equal a k-epoch fit
    x, y, xv, yv = _noisy()
    f = student.fit_net(x, y, epochs=8, val=(xv, yv), **_fit_kw())
    g = student.fit_net(x, y, epochs=f.best_epoch, val=None, **_fit_kw())
    assert g.best_epoch == f.best_epoch and g.val_losses == []
    assert all(np.array_equal(a, b) for la, lb in zip(f.layers, g.layers) for a, b in zip(la, lb))


def test_two_fits_in_one_process_give_identical_weight_bytes():
    x, y, xv, yv = _noisy()
    target = (np.arange(len(x)) % 9 - 4).astype(np.float32)  # logits for the distilled loss
    for loss, t in (("bce", y), ("mse", target)):
        a, b = (student.fit_net(x, t, epochs=3, val=(xv, yv), **_fit_kw(loss=loss)) for _ in range(2))
        sa, sb = (student.weights_sha256(Student("m", np.zeros(3, np.float32), np.ones(3, np.float32), f.layers))
                  for f in (a, b))
        assert sa == sb and a.val_losses == b.val_losses


def test_the_distilled_loss_fits_logits_and_the_last_bias_starts_where_asked():
    rng = np.random.default_rng(0)
    x = rng.standard_normal((500, 3)).astype(np.float32)
    target = 2 * x[:, 0] - x[:, 1]
    f = student.fit_net(x, target, widths=(8,), loss="mse", decay=0.0, batch=100, epochs=60, patience=1, bias=0.0,
                        lr=1e-2)
    z = student.forward(student.Cell(Student("m", np.zeros(3, np.float32), np.ones(3, np.float32), f.layers),
                                     sigmoid=False), x)
    assert np.sqrt(np.mean((z - target) ** 2)) < 0.3
    zero = student.fit_net(x, target, widths=(4,), loss="mse", decay=0.0, batch=100, epochs=0, patience=1, bias=1.5,
                           lr=1e-2)
    assert zero.layers[-1][1].tolist() == [1.5]  # no epoch ran: the bias is the starting value
    soft = student._soft(Student("t", np.zeros(3, np.float32), np.ones(3, np.float32),
                                 ((np.full((3, 1), 1e4, np.float32), np.zeros(1, np.float32)),)), x)
    assert soft.max() == np.float32(student.LOGIT_CLIP) and soft.min() == np.float32(-student.LOGIT_CLIP)


def test_weight_decay_is_on_the_weights_only():
    # all-zero inputs: the data gradient on the weights is 0 and the bias gradient does not depend on
    # the weights, so only a decay on the bias could make the two runs' biases differ
    _, y, _, _ = _noisy()
    x = np.zeros((len(y), 3), np.float32)
    kw = _fit_kw(widths=(), epochs=3, val=None, bias=5.0, lr=1e-2)
    plain = student.fit_net(x, y, **kw)
    decayed = student.fit_net(x, y, **{**kw, "decay": 10.0})
    assert not np.array_equal(plain.layers[0][0], decayed.layers[0][0])  # the decay moved the weights
    assert np.array_equal(plain.layers[0][1], decayed.layers[0][1])  # and left the bias alone
    assert plain.layers[0][1][0] != np.float32(5.0)  # the bias did move: equality is not a standstill


def test_reference_d_is_a_constant_when_no_iteration_helps():
    x = np.zeros((200, 2), np.float32)
    y = (np.arange(200) % 4 == 0).astype(np.int8)
    p, iterations = student.reference_regularised(x, y, x, y, Grid(hgb_d_iter=20, hgb_d_min_leaf=20))
    assert iterations == 0 and np.allclose(p, y.mean())


def test_the_budget_rule_applies_its_cuts_in_order_and_only_when_over_budget():
    def plan(epoch_s, budget=3 * 3600.0):
        p = student.Plan(Grid(budget_seconds=budget))
        p.first_epoch(1, epoch_s)
        return p

    # 4 fits x 15 epochs x 100 s = 6000 s, plus 4320 s for the rest: 10320 s, inside 3 h
    assert plan(100).applied == [] and plan(100).projected == 4 * 15 * 100 + 4320
    # 130 s: 12120 s; dropping reference (d) leaves 11220 s; 10 epochs leaves 8620 s
    p = plan(130)
    assert p.applied == ["drop_reference_d", "teacher_epochs_10"] and p.epochs == 10 and not p.reference_d
    assert p.decays == (1e-4, 1e-5)
    p = plan(200)
    assert p.applied == list(student.CUTS) and p.decays == (1e-4,) and p.epochs == 10
    p.first_epoch(1, 1.0)  # only the first teacher's first epoch counts
    assert p.applied == list(student.CUTS)
    q = student.Plan(Grid(budget_seconds=0))
    q.first_epoch(2, 1.0)  # not epoch 1: ignored
    assert q.applied == []


def test_ties_within_a_millionth_go_to_fewer_parameters_then_base_then_the_larger_decay():
    c = lambda loss, params, inputs="base", wd=1e-5: {"exact": loss, "parameters": params,  # noqa: E731
                                                      "inputs": inputs, "weight_decay": wd}
    key = student._student_key
    assert student.pick([c(0.1, 500), c(0.1 + 5e-7, 300), c(0.1 + 2e-6, 100)], key)["parameters"] == 300
    assert student.pick([c(0.1, 300, "trend"), c(0.1 + 1e-7, 300, "base")], key)["inputs"] == "base"
    assert student.pick([c(0.1, 100), c(0.2, 50)], key)["parameters"] == 100  # not tied: the lower loss wins
    # the comparison reads the unrounded loss ("exact"): 8e-7 apart is tied, 1.1e-6 apart is not
    assert student.pick([c(0.1234574, 300), c(0.1234566, 500)], key)["parameters"] == 300
    assert student.pick([c(0.1234571, 300), c(0.1234560, 500)], key)["parameters"] == 500
    teacher = student._teacher_key
    assert student.pick([c(0.1, 40, wd=1e-5), c(0.1 + 1e-7, 40, wd=1e-4)], teacher)["weight_decay"] == 1e-4


def test_what_ships_follows_the_fallbacks():
    decide = student.decide
    assert decide(True, True, True) == "distilled_student"
    assert decide(True, True, False) == "distilled_student"
    assert decide(False, True, True) == "hard_label_mlp"  # F1: the teacher did not earn the name
    assert decide(True, False, True) == "hard_label_mlp"
    assert decide(False, False, True) == "hard_label_mlp"
    assert decide(False, True, False) is None  # F2: nothing beats the constant
    assert decide(True, False, False) is None


def test_a_built_network_has_a_hidden_layer_and_passes_three_way_parity(built, tmp_path):
    shipped = [r for r in built.results.values() if r.onnx is not None]
    assert shipped, "no variant shipped on the tiny cohort"
    for r in shipped:
        m = r.meta
        assert m["shipped"] in ("distilled_student", "hard_label_mlp") and m["distilled"] == (m["shipped"] == "distilled_student")
        model = onnx.load_from_string(r.onnx)
        onnx.checker.check_model(model, full_check=True)
        assert model.opset_import[0].version == 13 and model.ir_version == 7
        assert set(student.onnx_ops(r.onnx)) == ALLOWED_OPS
        n = len(feature_names(r.temp, r.inputs))
        inp, outp = model.graph.input[0].type.tensor_type, model.graph.output[0].type.tensor_type
        assert inp.elem_type == onnx.TensorProto.FLOAT and [d.dim_value for d in inp.shape.dim] == [1, n]
        assert outp.elem_type == onnx.TensorProto.FLOAT and [d.dim_value for d in outp.shape.dim] == [1, 1]
        assert len(r.student.layers) == 2 and r.student.layers[0][0].shape[0] == n  # a hidden layer
        assert m["features_student"] == feature_names(r.temp, r.inputs) and m["onnx_sha256"] == student.sha256(r.onnx)
        assert tag_has_no_temperature(m["features_student"]) == (not r.temp)
        # the three forward passes, recomputed here on the same fixed training rows
        cell = student.Cell(r.student)
        cell.set_train(False)
        path = tmp_path / f"{m['tag']}.mindir"
        path.write_bytes(r.mindir)
        graph = nn.GraphCell(ms.load(str(path)))
        x = r.parity_x
        p_ms = student.forward(cell, x)
        p_ir = np.array([graph(Tensor(x[i:i + 1])).asnumpy()[0, 0] for i in range(len(x))], np.float32)
        p_onnx = student.onnx_predict(student.session(r.onnx), x)
        assert len(x) == min(TINY.parity_rows, m["training_rows"]) == m["parity_rows"]
        for a, b in ((p_ms, p_ir), (p_ms, p_onnx), (p_ir, p_onnx)):
            assert np.max(np.abs(a - b)) <= 1e-5
        assert p_onnx.min() >= 0 and p_onnx.max() <= 1 and m["parity_max_abs_diff"] <= 1e-5
        assert np.array_equal(p_ms, r.parity_p)


def tag_has_no_temperature(names):
    return not any(n.startswith("temperature") for n in names)


def test_parameter_counts_are_recomputed_from_the_weights_and_the_ratio_rule(built):
    for r in built.results.values():
        m, n_t = r.meta["parameters"], len(feature_names(r.temp, "teacher"))
        w = TINY.teacher_widths[0]
        assert r.teacher.parameters == m["teacher"] == sum(a.size + b.size for a, b in r.teacher.layers)
        assert m["teacher"] == n_t * w + w + w * w + w + w + 1
        assert r.meta["teacher_gate"]["much_bigger"] == (m["ratio_teacher_to_largest_distilled_student"] >= 10)
        if r.student is not None:
            n, h = len(r.student.mean), r.student.layers[0][0].shape[1]
            assert m["student"] == r.student.parameters == n * h + h + h + 1 and m["standardisation_constants"] == 2 * n
            assert m["ratio_teacher_to_student"] == round(m["teacher"] / m["student"], 6)
        for fit in r.meta["grid"]["distilled"]:
            n = len(feature_names(r.temp, fit["inputs"]))
            assert fit["parameters"] == n * fit["width"] + fit["width"] + fit["width"] + 1


def test_the_metadata_names_mindspore_and_records_the_grid_gates_and_counts(built, tfs, tmp_path, monkeypatch):
    monkeypatch.setattr(train_student, "GRID", TINY)
    train_student.write_outputs(tmp_path, built, tfs[:2], tfs[2:], {"ward_v2": [], "calibration": [305]}, True)
    text = (tmp_path / "metadata.json").read_text()
    meta = json.loads(text)
    assert meta["framework"] == "MindSpore" and meta["framework_version"] == ms.__version__
    assert meta["threads_num"] == 1 and meta["mode"] == "GRAPH_MODE" and meta["byte_reproducible"] is True
    assert meta["seeds_read"] == {"train": [61, 62], "validation": [261]} and meta["budget_cuts"] == []
    assert meta["packages"].keys() >= {"mindspore", "numpy", "scikit-learn", "onnx", "onnxruntime", "threadpoolctl"}
    assert meta["grid"]["student_widths"] == [4, 8] and meta["grid"]["teacher_decays"] == [1e-5, 1e-4]
    for m in meta["variants"].values():
        assert len(m["grid"]["teacher"]) == 2 and len(m["grid"]["distilled"]) == 4 == len(m["grid"]["hard_label"])
        assert set(m["teacher_gate"]) == {"constant", "old_recipe", "hard_label_mlp", "much_bigger", "passes"}
        assert set(m["references"]) == {"constant", "old_recipe", "hard_label_mlp", "regularised_gradient_boosting"}
        assert m["references"]["regularised_gradient_boosting"].keys() == {"val_log_loss", "iterations"}
        d = m["grid"]["distilled"][0]
        assert {"fidelity_rmse_log_odds", "log_loss_minus_teacher", "average_precision", "mean_p", "best_epoch",
                "val_log_loss_by_epoch"} <= set(d)
        assert len(d["val_log_loss_by_epoch"]) == d["epochs_run"] <= TINY.student_epochs
        assert m["teacher"]["chosen"]["best_epoch"] <= TINY.teacher_epochs and "train_log_loss" in m["teacher"]["chosen"]
        assert set(m["thresholds"]) == {"chart", "teacher", "student"}
    # no wall time and no path anywhere
    keys = re.findall(r'"([^"]+)":', text)
    allowed = {"budget_seconds", "onnxruntime"}  # the budget is an input, not a measurement
    assert not [k for k in keys if k not in allowed and re.search(r"wall|second|elapsed|duration|_s$|time", k)]
    assert str(tmp_path) not in text and not re.search(r'"/(app|tmp|Users|home|private|data)', text)
    assert "second" not in (tmp_path / "exports.json").read_text()


def _patch_run(monkeypatch, tfs, grid=TINY):
    monkeypatch.setattr(train_student, "load_for_training",
                        lambda folders, cache=None, workers=None: (tfs[:2], tfs[2:], {"ward_v2": [], "calibration": [305]}))
    monkeypatch.setattr(train_student, "GRID", grid)


def test_two_training_runs_write_the_same_bytes(tmp_path, monkeypatch, tfs):
    _patch_run(monkeypatch, tfs)
    # force the distilled branch here: on a tiny cohort the teacher gate cannot pass, and this run must
    # also cover the soft-target path and its export
    monkeypatch.setattr(student, "decide", lambda *a: "distilled_student")
    outs = []
    for name in ("one", "two"):
        assert train_student.main(["x", "--out", str(tmp_path / name), "--run_log", str(tmp_path / "logs" / f"{name}.json"),
                                   "--run_id", "tiny-1", "--commit", "abc1234"]) == 0  # loading is patched
        outs.append(tmp_path / name)
    logs = [json.loads((tmp_path / "logs" / f"{n}.json").read_text()) for n in ("one", "two")]
    assert all("time" in x for x in logs)
    for x in logs:
        x.pop("time")  # every wall-clock number lives under `time`; the rest must be identical
    assert json.dumps(logs[0], sort_keys=True) == json.dumps(logs[1], sort_keys=True)
    assert logs[0]["run_id"] == "tiny-1" and logs[0]["commit"] == "abc1234"
    names = sorted(p.name for p in outs[0].iterdir())
    assert names == sorted(p.name for p in outs[1].iterdir())
    assert {"metadata.json", "exports.json", "timing.json", "model.onnx", "model.mindir", "model_no_temp.onnx"} <= set(names)
    for name in names:
        if name not in ("timing.json", "exports.json") and not name.endswith(".mindir"):
            assert (outs[0] / name).read_bytes() == (outs[1] / name).read_bytes(), name  # weights, ONNX, metadata
    meta = json.loads((outs[0] / "metadata.json").read_text())
    for tag, m in meta["variants"].items():
        assert m["shipped"] == "distilled_student" and m["distilled"] and m["student"]["loss"] == "mse"
        assert m["onnx_sha256"] == student.sha256((outs[0] / train_student.FILES[tag]).read_bytes())
        for out in outs:  # MindIR bytes are not promised; its sha256 is in exports.json and it loads
            ex = json.loads((out / "exports.json").read_text())["variants"][tag]
            data = (out / train_student.MINDIR[tag]).read_bytes()
            assert ex["mindir_sha256"] == student.sha256(data) and ex["mindir_bytes"] == len(data)
            assert nn.GraphCell(ms.load(str(out / train_student.MINDIR[tag]))) is not None
    timing = json.loads((outs[0] / "timing.json").read_text())
    for tag in meta["variants"]:
        assert timing[tag]["intra_op_num_threads"] == 1 and timing[tag]["timed_calls"] == 10_000
        assert timing[tag]["inter_op_num_threads"] == 1 and timing[tag]["warmup_calls"] == 1000
    assert "projected_total_s" in timing["run"]


def _fake(built, exported: bool = False):
    if exported:
        return built
    results = {t: replace(r, onnx=None, mindir=None, student=None, inputs=None, timing_row=None, exports=None,
                          meta={**r.meta, "shipped": None, "distilled": False, "student": None, "onnx_sha256": None})
               for t, r in built.results.items()}
    return student.Built(results, [], {})


def test_a_variant_that_is_not_exported_leaves_no_old_model_file(tmp_path, monkeypatch, built, tfs):
    _patch_run(monkeypatch, tfs)
    monkeypatch.setattr(train_student, "build", lambda *a, **k: _fake(built))
    olds = ["model.onnx", "model.mindir", "model.ms", "model_no_temp.onnx", "model_no_temp.mindir", "model_no_temp.ms"]
    for name in olds:
        (tmp_path / name).write_bytes(b"old")
    assert train_student.main(["x", "--out", str(tmp_path)]) == 0
    assert not [n for n in olds if (tmp_path / n).exists()]
    assert set(json.loads((tmp_path / "timing.json").read_text())) == {"run"}
    meta = json.loads((tmp_path / "metadata.json").read_text())
    assert all(m["shipped"] is None and m["files"] is None for m in meta["variants"].values())


def test_data_url_and_train_url_give_the_same_files_as_folders_and_out(tmp_path, monkeypatch, built, tfs):
    seen = []
    monkeypatch.setattr(train_student, "load_for_training",
                        lambda folders, cache=None, workers=None: (seen.append(list(folders)), (tfs[:2], tfs[2:], {}))[1])
    monkeypatch.setattr(train_student, "build", lambda *a, **k: built)
    monkeypatch.setattr(train_student, "GRID", TINY)
    data = tmp_path / "data"
    for name in ("t2", "t1"):
        (data / name).mkdir(parents=True)
    (data / "readme.txt").write_text("not a trial")
    assert train_student.main([str(data / "t1"), str(data / "t2"), "--out", str(tmp_path / "a")]) == 0
    assert train_student.main(["--data_url", str(data), "--train_url", str(tmp_path / "b")]) == 0
    assert seen[0] == seen[1] == [str(data / "t1"), str(data / "t2")]  # every sub-folder, sorted
    for name in sorted(p.name for p in (tmp_path / "a").iterdir()):
        if name != "timing.json":
            assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name
    assert train_student.main(["--out", str(tmp_path / "c"), "--train_url", str(tmp_path / "c"), "--data_url", str(data)]) == 0
    for argv in (["x", "--data_url", str(data)], ["--data_url", str(data), "--train_url", "a", "--out", "b"],
                 ["--data_url", str(tmp_path / "missing")], ["--byte_reproducible", "maybe", "x"]):
        assert train_student.main(argv) == 2


def test_byte_reproducible_is_written_from_the_flag(tmp_path, monkeypatch, built, tfs):
    _patch_run(monkeypatch, tfs)
    monkeypatch.setattr(train_student, "build", lambda *a, **k: built)
    assert train_student.main(["x", "--out", str(tmp_path), "--byte_reproducible", "false"]) == 0
    assert json.loads((tmp_path / "metadata.json").read_text())["byte_reproducible"] is False


def test_the_training_code_imports_no_network_module_and_names_no_local_path():
    root = Path(student.__file__).parent
    banned = re.compile(r"^\s*(import|from)\s+(urllib|socket|requests|http|ftplib|smtplib|ssl)\b", re.M)
    local = re.compile(r"/(Users|home|private|Volumes|tmp|var)/")
    for name in ("student.py", "train_student.py", "evaluate_student.py"):
        text = (root / name).read_text()
        assert not banned.search(text), name
        assert not local.search(text), name
        assert "shell=True" not in text and "os.environ" not in text, name


# --- the inputs for the hand-run MindSpore Lite check ---

def test_lite_rows_are_written_as_inputs_and_calibration_files_and_nothing_else(tmp_path, monkeypatch, built, tfs):
    _patch_run(monkeypatch, tfs)
    monkeypatch.setattr(train_student, "build", lambda *a, **k: built)
    assert train_student.main(["x", "--out", str(tmp_path / "o")]) == 0
    assert not (tmp_path / "rows").exists() and not list((tmp_path / "o").glob("*.ms"))  # off by default
    ex = json.loads((tmp_path / "o" / "exports.json").read_text())
    assert ex["lite"].startswith("made by hand with convert_lite.sh") and "/" not in ex["lite"].replace("convert_lite.sh", "")
    assert train_student.main(["x", "--out", str(tmp_path / "o"), "--lite_rows", str(tmp_path / "rows")]) == 0
    shipped = [r for r in built.results.values() if r.onnx is not None]
    assert sorted(p.name for p in (tmp_path / "rows").iterdir()) == sorted(
        Path(train_student.MINDIR[r.tag]).stem for r in shipped)
    for r in shipped:
        folder = tmp_path / "rows" / Path(train_student.MINDIR[r.tag]).stem
        k = min(20, len(r.parity_x))
        assert len(list(folder.glob("row*.bin"))) == len(list(folder.glob("row*.txt"))) == k
        n = len(feature_names(r.temp, r.inputs))
        for i in range(k):
            x = np.frombuffer((folder / f"row{i}.bin").read_bytes(), np.float32)
            assert x.shape == (n,) and np.array_equal(x, r.parity_x[i])  # float32 [1, n]
            head, value = (folder / f"row{i}.txt").read_text().splitlines()
            assert head == "@OUTPUT@ 2 1 1" and np.float32(float(value)) == r.parity_p[i]  # round trip
    assert train_student.main(["--lite_rows"]) == 2  # a flag needs its value


# --- the evaluator's refit ---

def test_the_refit_reproduces_the_built_weights_and_a_wrong_hash_is_refused(tmp_path, monkeypatch, built, tfs):
    monkeypatch.setattr(train_student, "GRID", TINY)
    train_student.write_outputs(tmp_path, built, tfs[:2], tfs[2:], {}, True)
    meta = json.loads((tmp_path / "metadata.json").read_text())
    m = meta["variants"]["all"]
    refit = student.refit_variant(tfs[:2], True, m, Grid.from_dict(meta["grid"]))
    assert student.weights_sha256(refit["teacher"]) == m["teacher"]["weights_sha256"]
    assert student.weights_sha256(refit["student"]) == m["weights_sha256"]
    assert student.sha256(to_onnx(refit["student"])) == m["onnx_sha256"]
    policies, notes = evaluate_student.student_policies(tfs[:2], [], meta, tmp_path)
    assert notes and all(p.name.split("_no_temp")[0] in ("teacher", "student", "news2_or_student") for p in policies)
    bad = json.loads(json.dumps(meta))
    bad["variants"]["all"]["teacher"]["weights_sha256"] = "0" * 64
    with pytest.raises(TrialError, match="teacher's weight sha256"):
        evaluate_student.student_policies(tfs[:2], [], bad, tmp_path)
    bad = json.loads(json.dumps(meta))
    bad["variants"]["all"]["weights_sha256"] = "0" * 64
    with pytest.raises(TrialError, match="student's weight sha256"):
        evaluate_student.student_policies(tfs[:2], [], bad, tmp_path)
    (tmp_path / "model.onnx").write_bytes(b"changed")
    with pytest.raises(TrialError, match="committed file's sha256"):
        evaluate_student.student_policies(tfs[:2], [], meta, tmp_path)
    (tmp_path / "model.mindir").write_bytes(b"changed")
    with pytest.raises(TrialError, match="differs from exports.json"):
        evaluate_student._check_files(tmp_path)


def test_the_students_threshold_and_validation_loss_come_from_the_onnx_output(built, tfs):
    val = tfs[2:]
    shipped = [r for r in built.results.values() if r.onnx is not None]
    assert shipped
    for r in shipped:
        sess = student.session(r.onnx)
        probs = [student.onnx_predict(sess, view(tf.x, r.temp, r.inputs)) for tf in val]
        chart = chart_burden(val)
        burden = model_burden(val, probs)
        t = match_threshold(burden, chart[0])
        rec = r.meta["thresholds"]["student"]
        assert rec["threshold"] == t and rec["alerts"] == (None if t is None else burden[t][0])
        assert rec["bins_alerting"] == (None if t is None else burden[t][1])
        x, y, _ = student._stack(val, r.inputs, r.temp)
        onnx_loss = student.log_loss(y, student.onnx_predict(sess, x))
        assert r.meta["student"]["val_log_loss_onnx"] == round(onnx_loss, 6)
        assert abs(onnx_loss - r.meta["student"]["val_log_loss"]) < 1e-5  # selection used MindSpore's p: same to 1e-5


# --- one scoring function for every model compared ---

def test_the_teacher_and_a_reference_with_the_same_probabilities_get_the_same_loss(built, tfs):
    r = built.results["all"]
    xv, yv, gv = student._stack(tfs[2:], "teacher", True)
    z = student.forward(student.Cell(r.teacher, sigmoid=False), xv)
    assert r.meta["teacher"]["val_log_loss"] == round(float(student.row_loss(yv, student._sigmoid(z)).mean()), 6)
    for scale in (1, 25):  # 25: logits far outside the +-9.2102 that the soft targets are clipped to
        zz = z * np.float32(scale)
        p = student._sigmoid(zz)
        gate, _ = student.teacher_gate(yv, gv, zz, float(yv.mean()), p, p, 100.0)
        assert gate["old_recipe"]["difference"] == 0.0 == gate["hard_label_mlp"]["difference"]
    assert abs(zz).max() > student.LOGIT_CLIP


def test_a_confidently_wrong_positive_fails_the_gate_only_without_the_old_clip():
    n = 100
    yv, gv = np.zeros(n, np.int8), np.arange(n)
    yv[0] = 1
    z = np.full(n, -4.6)
    z[0] = -15.0  # sure it is negative, and it is a positive: p about 3e-7
    p_ref = np.full(n, 0.06)
    p_ref[0] = 1.2e-4  # the reference is less sure
    gate, passes = student.teacher_gate(yv, gv, z, 0.5, p_ref, p_ref, 100.0)
    assert not passes and not gate["old_recipe"]["passes"]
    clipped, clipped_passes = student.teacher_gate(yv, gv, np.clip(z, -student.LOGIT_CLIP, student.LOGIT_CLIP),
                                                   0.5, p_ref, p_ref, 100.0)
    assert clipped_passes and clipped["old_recipe"]["passes"]  # what the old clip would have let through


# --- mutations that used to pass every test ---

def test_the_distilled_fit_gets_the_teachers_logits_and_never_the_labels(tfs, built, monkeypatch):
    g = replace(TINY, teacher_decays=(1e-4,), student_widths=(4,), reference_d=False)
    calls, known = [], {}

    def fake_soft(teacher, xt):  # a teacher whose logits are a known function unrelated to the labels
        known["soft"] = (np.sin(np.arange(len(xt)) / 7.0) * 3).astype(np.float32)
        return known["soft"]

    real = student._fit_student

    def spy(xs, target, loss, *a, **k):
        calls.append((loss, np.array(target)))
        return real(xs, target, loss, *a, **k)

    monkeypatch.setattr(student, "_soft", fake_soft)
    monkeypatch.setattr(student, "_fit_student", spy)
    student.build(tfs[:2], tfs[2:], g)
    labels = student._stack(tfs[:2], "base", True)[1]
    mse = [t for loss, t in calls if loss == "mse"]
    bce = [t for loss, t in calls if loss == "bce"]
    assert len(mse) == len(bce) == 4 and all(len(t) == len(labels) for t in mse)  # 2 variants x 2 inputs
    assert all(np.array_equal(t, known["soft"]) for t in mse)  # the teacher's logits
    assert not any(np.array_equal(t, labels) or set(np.unique(t)) <= {0, 1} for t in mse)  # never the labels
    assert all(np.array_equal(t, labels) for t in bce)  # reference (c) is the one on labels
    # the evaluator's refit builds its soft targets the same way
    calls.clear()
    meta = {**built.results["all"].meta, "student": {"inputs": "base", "width": 4, "loss": "mse", "best_epoch": 1}}
    student.refit_variant(tfs[:2], True, meta, g)
    assert [loss for loss, _ in calls] == ["mse"] and np.array_equal(calls[0][1], known["soft"])


def test_the_gate_uses_the_upper_percentile_so_an_interval_across_zero_does_not_pass():
    ref = np.zeros(8)
    loss = np.array([-1.0, -1.0, 0.4, 0.4, 0.3, 0.3, -0.2, -0.2])  # patient sums -2, +0.8, +0.6, -0.4
    got = paired_gate(loss, {"r": ref}, np.repeat([1, 2, 3, 4], 2))["r"]
    assert got["difference"] < 0 and got["lower_2_5"] < 0 < got["upper_97_5"]
    assert got["passes"] is False  # the lower end alone is under 0 and must not pass


def test_the_best_epoch_weights_are_kept_when_the_validation_loss_rises_afterwards(monkeypatch):
    x, y, xv, yv = _noisy()
    seq = iter([0.5, 0.3, 0.4, 0.45])  # the minimum is epoch 2
    with monkeypatch.context() as m:
        m.setattr(student, "log_loss", lambda *a: next(seq))
        f = student.fit_net(x, y, epochs=4, val=(xv, yv), **_fit_kw(patience=5))
    assert f.best_epoch == 2 and f.val_losses == [0.5, 0.3, 0.4, 0.45]
    two = student.fit_net(x, y, epochs=2, val=None, **_fit_kw())  # the refit for the recorded best epoch
    four = student.fit_net(x, y, epochs=4, val=None, **_fit_kw())
    same = lambda a, b: all(np.array_equal(p, q) for la, lb in zip(a.layers, b.layers) for p, q in zip(la, lb))  # noqa: E731
    assert same(f, two) and not same(f, four)  # not the last epoch's weights


def test_the_ten_times_rule_is_part_of_the_teacher_gate():
    n = 100
    yv, gv = (np.arange(n) % 2).astype(np.int8), np.arange(n)
    z = np.where(yv == 1, 4.0, -4.0)  # a good teacher
    p_ref = np.full(n, 0.5)
    _, passes = student.teacher_gate(yv, gv, z, 0.5, p_ref, p_ref, 10.0)
    assert passes
    gate, passes = student.teacher_gate(yv, gv, z, 0.5, p_ref, p_ref, 9.99)
    assert not passes and all(v["passes"] for v in gate.values())  # every comparison passed; the ratio alone fails it


def test_the_constant_reference_is_at_the_training_prevalence(tfs):
    y = tfs[2].y.copy()
    y[np.flatnonzero(y == 0)[::4]] = 1  # a validation cohort with a much higher prevalence than training
    val = [replace(tfs[2], y=y)]
    g = replace(TINY, teacher_decays=(1e-4,), student_widths=(4,), reference_d=False)
    for tag, r in student.build(tfs[:2], val, g).results.items():
        ytr = student._stack(tfs[:2], "base", r.temp)[1]
        yv = student._stack(val, "base", r.temp)[1]
        train_rate, val_rate = float(ytr.mean()), float(yv.mean())
        assert val_rate > 3 * train_rate
        loss = lambda rate: round(float(student.row_loss(yv, np.full(len(yv), rate)).mean()), 6)  # noqa: E731
        assert r.meta["references"]["constant"]["val_log_loss"] == loss(train_rate) != loss(val_rate)
        assert r.meta["training_positive_rate"] == round(train_rate, 6)


# --- the pre-registered settings ---

def test_the_default_grid_is_the_pre_registered_one():
    g = train_student.GRID  # the command's grid
    assert g == student.Grid()
    assert g.teacher_widths == (256, 256) and g.teacher_decays == (1e-5, 1e-4)
    assert (g.teacher_batch, g.teacher_epochs, g.teacher_patience) == (1024, 15, 3)
    assert g.student_widths == (8, 16, 32) and g.student_inputs == ("base", "trend")
    assert (g.student_batch, g.student_epochs, g.student_patience, g.student_decay) == (4096, 8, 2, 1e-5)
    assert g.learning_rate == 1e-3 and g.parity_rows == 1000 and g.budget_seconds == 3 * 3600
    assert (g.hgb_b_iter, g.hgb_d_iter, g.hgb_d_min_leaf, g.reference_d) == (500, 2000, 2000, True)
    assert bootstrap.RESAMPLES == 1000 and bootstrap.SEED == 20261112
    assert inspect.signature(student.paired_gate).parameters["resamples"].default == 1000
    assert student.CLIP == 1e-4 and round(student.LOGIT_CLIP, 4) == 9.2102
    assert student.MS_THREADS == 1 and student.PARITY_TOL == 1e-5 and student.TIE == 1e-6 and student.THREADS == 4
    expect = [i / 1000 for i in range(1, 10)] + [i / 100 for i in range(1, 100)]
    assert list(student.THRESHOLDS) == expect and len(expect) == 108
    assert student.TRAIN_SEEDS == range(61, 161) and student.VAL_SEEDS == range(261, 301)
    assert (student.OPSET, student.IR_VERSION) == (13, 7)


def test_the_last_bias_starts_at_the_prevalence_log_odds_and_the_students_at_the_mean_target():
    x, y, _, _ = _noisy()
    log = lambda s: None  # noqa: E731
    t = student._fit_teacher(x, y, TINY, 1e-4, 0, None, log, "t")  # zero epochs: the starting values
    rate = float(y.mean())
    assert t.layers[-1][1].tolist() == [np.float32(np.log(rate / (1 - rate)))]
    soft = (np.arange(len(x)) % 9 - 3).astype(np.float32)
    d = student._fit_student(x, soft, "mse", 4, TINY, None, log, "d", epochs=0)
    assert d.layers[-1][1].tolist() == [np.float32(soft.mean())]
    h = student._fit_student(x, y, "bce", 4, TINY, None, log, "h", epochs=0)
    assert h.layers[-1][1].tolist() == [np.float32(np.log(rate / (1 - rate)))]


def test_the_built_standardisation_constants_are_the_training_rows_moments(built, tfs):
    for r in built.results.values():
        xt = student._stack(tfs[:2], "teacher", r.temp)[0]
        mean, std = student._moments(xt)
        assert np.array_equal(r.teacher.mean, mean) and np.array_equal(r.teacher.std, std)
        if r.student is not None:
            xs = student._stack(tfs[:2], r.inputs, r.temp)[0]
            mean, std = student._moments(xs)
            assert np.array_equal(r.student.mean, mean) and np.array_equal(r.student.std, std)


def test_an_epoch_is_timed_with_its_validation_pass(monkeypatch):
    x, y, xv, yv = _noisy()
    clock, seen = [0.0], []
    real = student.forward

    def slow(cell, rows):
        clock[0] += 100.0  # the validation forward pass "takes" 100 s
        return real(cell, rows)

    monkeypatch.setattr(student, "timeit", types.SimpleNamespace(default_timer=lambda: clock[0]))
    monkeypatch.setattr(student, "forward", slow)
    student.fit_net(x, y, epochs=1, val=(xv, yv), on_epoch=lambda e, secs: seen.append(secs), **_fit_kw())
    student.fit_net(x, y, epochs=1, val=None, on_epoch=lambda e, secs: seen.append(secs), **_fit_kw())
    assert seen == [100.0, 0.0]


# --- the evaluator trusts nothing it can check ---

def _trained(out: Path, monkeypatch, built, tfs, reproducible: bool) -> Path:
    monkeypatch.setattr(train_student, "GRID", TINY)
    train_student.write_outputs(out, built, tfs[:2], tfs[2:], {}, reproducible)
    return out


def test_a_changed_byte_in_metadata_json_is_refused(tmp_path, monkeypatch, built, tfs):
    out = _trained(tmp_path / "out", monkeypatch, built, tfs, True)
    evaluate_student._check_files(out)  # untouched: fine
    meta = json.loads((out / "metadata.json").read_text())
    thr = meta["variants"]["all"]["thresholds"]["student"]
    thr["threshold"] = 0.75 if thr["threshold"] != 0.75 else 0.74  # one threshold nudged
    (out / "metadata.json").write_bytes(train_student._json_bytes(meta))
    with pytest.raises(TrialError, match="metadata.json: sha256 differs"):
        evaluate_student._check_files(out)
    held, tr = ext_folder(tmp_path, "h", 11), ext_folder(tmp_path, "t", 61)
    with pytest.raises(TrialError, match="metadata.json: sha256 differs"):
        evaluate_student.run([str(held), str(tr)], out=out, workers=1)


def test_the_json_flag_writes_the_held_out_json_and_refuses_an_unwritable_path_first(tmp_path, monkeypatch, capsys):
    calls = []

    def fake(folders, out, cache, workers, result=None):
        calls.append(result)
        if result is not None:
            result.update({"format": "biosentry-held-out/1", "rules": [], "context": {}})
        return "the text\n"

    monkeypatch.setattr(evaluate_student, "run", fake)
    path = tmp_path / "held.json"
    assert evaluate_student.main(["x", "--json", str(path)]) == 0 and calls[-1] == json.loads(path.read_text())
    assert capsys.readouterr().out == "the text\n"
    assert evaluate_student.main(["x"]) == 0 and calls[-1] is None and capsys.readouterr().out == "the text\n"
    n = len(calls)
    assert evaluate_student.main(["x", "--json", str(tmp_path / "no" / "h.json")]) == 2 and len(calls) == n


def test_exact_mode_reads_no_validation_folder_but_the_fallback_does(tmp_path, monkeypatch, opened, built, tfs):
    log, note = opened
    note.root = tmp_path
    exact = _trained(tmp_path / "o1", monkeypatch, built, tfs, True)
    fallback = _trained(tmp_path / "o2", monkeypatch, built, tfs, False)
    folders = [str(ext_folder(tmp_path, "h", 11)), str(ext_folder(tmp_path, "t", 61)),
               str(ext_folder(tmp_path, "v", 261))]
    seen = {}
    for name, out in (("exact", exact), ("fallback", fallback)):
        log.clear()
        try:
            evaluate_student.run(folders, out=out, workers=1)
        except TrialError:
            pass  # the toy trial cannot refit a real model; only what was opened matters here
        seen[name] = {n for folder, n in log if folder == "v"}
    assert seen["exact"] == {"manifest.json"}
    assert seen["fallback"] == set(trials.FILES)


def test_the_fallback_requires_the_onnx_validation_loss_within_a_millionth(tmp_path, monkeypatch, built, tfs):
    out = _trained(tmp_path / "out", monkeypatch, built, tfs, False)
    meta = json.loads((out / "metadata.json").read_text())
    tag = next(t for t, m in meta["variants"].items() if m["student"] is not None)
    evaluate_student.student_policies(tfs[:2], tfs[2:], meta, out)  # the recorded value: accepted
    near = json.loads(json.dumps(meta))
    near["variants"][tag]["student"]["val_log_loss_onnx"] += 3e-7  # inside 1e-6 even with the rounding
    evaluate_student.student_policies(tfs[:2], tfs[2:], near, out)
    far = json.loads(json.dumps(meta))
    far["variants"][tag]["student"]["val_log_loss_onnx"] += 2e-6
    with pytest.raises(TrialError, match="validation log loss"):
        evaluate_student.student_policies(tfs[:2], tfs[2:], far, out)


# --- the run log ---

def test_the_run_log_has_every_fit_with_per_epoch_training_and_validation_loss_and_no_path(built, tfs, tmp_path, monkeypatch):
    monkeypatch.setattr(train_student, "GRID", TINY)
    top = train_student.write_outputs(tmp_path / "out", built, tfs[:2], tfs[2:], {}, True)
    log = train_student.run_log(top, built, "tiny-1", "abc1234")
    text = json.dumps(log, indent=1, sort_keys=True)
    assert {"model", "run_id", "commit", "framework", "framework_version", "packages", "settings", "data", "variants",
            "budget_cuts", "time"} <= set(log)
    assert log["model"] == "tier_c/student_vitals" and log["framework"] == "MindSpore"
    assert log["settings"]["grid"]["student_widths"] == [4, 8] and log["settings"]["threads_num"] == 1
    assert log["data"]["train_seeds"] == [61, 62] and log["data"]["validation_seeds"] == [261]
    for tag, v in log["variants"].items():
        rows = log["data"]["variants"][tag]
        assert rows["training_positives"] > 0 and rows["validation_positives"] > 0 and rows["training_rows"] > 0
        kinds = [f["kind"] for f in v["fits"]]
        assert kinds.count("teacher") == 2 and kinds.count("distilled") == 4 and kinds.count("hard_label") == 4
        assert len({f["name"] for f in v["fits"]}) == len(v["fits"])
        for f in v["fits"]:
            n = len(f["val_log_loss_by_epoch"])
            assert n >= 1 and len(f["train_loss_by_epoch"]) == n and 1 <= f["best_epoch"] <= n  # both, every epoch
            assert f["final_val_log_loss"] == min(f["val_log_loss_by_epoch"]) and f["parameters"] > 0
            assert {"inputs", "width", "weight_decay", "average_precision"} <= set(f)
            t = log["time"]["fits"][f["name"]]
            assert len(t["epoch_seconds"]) == n and t["seconds"] >= 0
        assert v["references"]["old_recipe"]["iterations"] > 0 and "iterations" in v["references"]["regularised_gradient_boosting"]
        assert {"teacher_gate", "chosen", "thresholds"} <= set(v)
    assert "total_seconds" in log["time"]
    assert str(tmp_path) not in text and not re.search(r'"/(app|tmp|Users|home|private|data)', text)
    stripped = {k: v for k, v in log.items() if k != "time"}
    assert not re.search(r"seconds|wall|elapsed", json.dumps(stripped).replace("budget_seconds", ""))  # no wall-clock number outside `time`


def test_the_run_log_flags_are_checked_and_parent_folders_are_made(tmp_path, monkeypatch, built, tfs):
    _patch_run(monkeypatch, tfs)
    monkeypatch.setattr(train_student, "build", lambda *a, **k: built)
    for bad in (["--run_id", "has space"], ["--run_id", "a/b"], ["--run_id", "x" * 65], ["--commit", "two words"]):
        assert train_student.main(["x", "--out", str(tmp_path / "o"), "--run_log", str(tmp_path / "l.json"), *bad]) == 2
    assert not (tmp_path / "o").exists() and not (tmp_path / "l.json").exists()
    assert train_student.main(["x", "--out", str(tmp_path / "o"), "--run_log", str(tmp_path / "a" / "b" / "l.json")]) == 0
    log = json.loads((tmp_path / "a" / "b" / "l.json").read_text())
    assert log["run_id"] is None and log["commit"] is None
    assert train_student.main(["x", "--out", str(tmp_path / "p")]) == 0 and not (tmp_path / "p" / "l.json").exists()
