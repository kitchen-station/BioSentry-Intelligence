import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import onnx
import pytest

from cohort import stay, trial, vitals_row
from research import student, train_student, trials
from research.student import (
    Student, build, feature_names, load_for_training, match_threshold, patient_features,
    patient_labels, prob_policy, to_onnx, view,
)
from research.trials import Episode, TrialError

TOY = Path(__file__).parent.parent / "tests" / "data" / "toy"  # seed 11: a held-out seed
ALLOWED_OPS = {"Sub", "Div", "Gemm", "Relu", "Sigmoid"}  # exactly the ops the graph uses


@pytest.fixture(scope="module")
def built():
    return build([trial(1), trial(2)], [trial(8)])


# --- causality ---

def test_changing_a_later_bin_changes_no_earlier_feature_row():
    rows, _ = stay("p-000", 0, 1, episode=True)
    minutes, x = patient_features(rows)
    for k in (10, 40, 90):  # before the baseline forms, just after, and late
        m = minutes[k]
        edits = [
            {**rows, m: vitals_row("p-000", m, 150, 30, 85, 39.5)},  # a later bin changed
            {mm: r for mm, r in rows.items() if mm != m},  # a later bin removed
            {**rows, m: vitals_row("p-000", m, 70, 16, None)},  # a later bin turned NOT RANKED
        ]
        for edited in edits:
            em, ex = patient_features(edited)
            assert em[:k] == minutes[:k]
            assert np.array_equal(ex[:k], x[:k])


# --- labels ---

def test_label_windows_start_to_fully_in_positive_after_excluded():
    e = Episode("p", 15, None, "respiratory", 100.5, 30.0)  # fully in at 130.5
    assert patient_labels([90, 105, 120, 135, 600], e).tolist() == [0, 1, 1, -1, -1]
    assert patient_labels([105, 120], Episode("p", 15, None, None, None, None)).tolist() == [0, 0]
    late = Episode("p", 15, 200, "respiratory", 900.0, 30.0)  # starts after the stay
    assert patient_labels([15, 30, 195], late).tolist() == [0, 0, 0]


def test_starting_patients_and_excluded_bins_are_not_training_rows():
    t = trial(1, patients=2)
    x, y = student.cohort([t])
    _, e = stay("p-000", 0, 1, episode=True)
    rows = {r.minute: r for r in t.rows if r.patient == "p-000"}
    labels = patient_labels(patient_features(rows)[0], e)
    assert len(y) == (labels >= 0).sum() + 120 and set(y.tolist()) == {0, 1}
    starting = replace(t, episodes=tuple(replace(ep, admit_minute=0) for ep in t.episodes))
    assert len(student.cohort([starting])[1]) == 0


# --- missing handling ---

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
    s, sm = names.index("spo2_z"), names.index("spo2_z_missing")
    assert gx[22, sm] == 1.0 and gx[23, sm] == 0.0  # SpO2 needs one more bin to form
    hr = names.index("heart_rate_z_missing")
    assert gx[22, hr] == 0.0  # heart rate was present in that bin and formed on time
    assert not np.isnan(gx).any()
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

def test_no_temperature_model_has_no_temperature_input(built):
    names = feature_names(False)
    assert len(names) == 15 and not any(n.startswith("temperature") for n in names)
    rows, _ = stay("p-000", 0, 1, episode=True)
    hot = {m: vitals_row("p-000", m, r.heart_rate, r.respiratory_rate, r.spo2, r.temperature + 2.5)
           for m, r in rows.items()}  # 39.3 C and up: NEWS2 temperature points change
    (_, a), (_, b) = patient_features(rows), patient_features(hot)
    assert not np.array_equal(view(a, True), view(b, True))
    assert np.array_equal(view(a, False), view(b, False))
    model = onnx.load_from_string(built["no_temp"]["onnx"])
    assert [d.dim_value for d in model.graph.input[0].type.tensor_type.shape.dim] == [1, 15]
    assert built["no_temp"]["meta"]["features"] == names


# --- held-out seeds ---

def test_held_out_folder_is_skipped_after_reading_only_its_manifest(tmp_path, monkeypatch):
    held = shutil.copytree(TOY, tmp_path / "held")
    opened = []
    real = trials._read
    monkeypatch.setattr(trials, "_read", lambda folder, name, cap: (opened.append(name), real(folder, name, cap))[1])
    with pytest.raises(TrialError, match="need at least one trial"):
        load_for_training([str(held)])
    assert opened == ["manifest.json"]
    assert train_student.main([str(held), "--out", str(tmp_path / "o")]) == 2
    assert not (tmp_path / "o").exists()


def test_build_refuses_held_out_trials():
    with pytest.raises(TrialError, match="not in seeds 1 to 7"):
        build([trial(11)], [trial(8)])
    with pytest.raises(TrialError, match="not in seeds 8 to 10"):
        build([trial(1)], [trial(12)])
    with pytest.raises(TrialError, match="not in seeds 8 to 10"):
        build([trial(1)], [trial(3)])


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
    inp = model.graph.input[0].type.tensor_type
    assert inp.elem_type == onnx.TensorProto.FLOAT and [d.dim_value for d in inp.shape.dim] == [1, 5]
    x = np.vstack([
        np.sin(np.arange(5000, dtype=np.float64)).reshape(1000, 5) * 40,
        (np.arange(5000).reshape(1000, 5) * 7 % 3 == 0).astype(np.float64),  # 0/1 flags
        np.cos(np.arange(5000, dtype=np.float64)).reshape(1000, 5) * 1e4,  # extremes near +-1e4
    ])
    got = student.onnx_predict(student.session(data), x)
    assert np.max(np.abs(got - s.forward(x))) <= 1e-5


def test_built_students_pass_parity_and_use_allowed_ops(built):
    for tag in ("all", "no_temp"):
        m = built[tag]["meta"]
        assert m["parity_max_abs_diff"] <= 1e-5 and m["parity_rows"] == min(1000, m["training_rows"])
        relu = set() if m["student_kind"] == "mlp" else {"Relu"}  # a logistic student has no hidden layer
        assert set(m["onnx_ops"]) == ALLOWED_OPS - relu and m["onnx_sha256"] == student.sha256(built[tag]["onnx"])
        assert m["student_kind"] in ("logistic", "mlp") and set(m["validation_seeds_8_10"]) == {"teacher", "logistic", "mlp"}


# --- determinism ---

def test_two_runs_give_the_same_onnx_bytes(built):
    again = build([trial(1), trial(2)], [trial(8)])
    for tag in ("all", "no_temp"):
        assert again[tag]["onnx"] == built[tag]["onnx"]
        assert again[tag]["meta"] == built[tag]["meta"]


# --- threshold matching ---

def test_match_threshold_is_the_smallest_within_the_news2_burden():
    assert match_threshold({0.1: 5, 0.2: 3, 0.3: 4}, 4) == 0.2  # not monotone: still the smallest that fits
    assert match_threshold({0.1: 4, 0.2: 3}, 4) == 0.1  # equal burden is allowed
    assert match_threshold({0.1: 9, 0.2: 5}, 4) is None


def test_alert_counts_follow_the_alert_rule():
    t = trial(8)
    constant = lambda p: (lambda rows: {m: p for m in rows})  # noqa: E731
    alerts, chart, days = student.alerts_by_threshold([t], constant(0.5))
    assert days == t.patient_days
    assert all(n == 8 for th, n in alerts.items() if th <= 0.5)  # alerting from the first bin: one each
    assert all(n == 0 for th, n in alerts.items() if th > 0.5)
    assert chart >= 0


def test_standardisation_uses_the_training_seeds_only(built):
    x, _ = student.cohort([trial(1), trial(2)])
    for tag, temp in (("all", True), ("no_temp", False)):
        a = view(x, temp)
        std = a.std(axis=0)
        std[std == 0] = 1.0
        s = built[tag]["student"]
        assert np.array_equal(s.mean, a.mean(axis=0).astype(np.float32))
        assert np.array_equal(s.std, std.astype(np.float32))
