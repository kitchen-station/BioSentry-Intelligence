import builtins
import json
import os
import re
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "tests"))  # the shared text-table helpers
from cohort import trial, vitals_row  # noqa: E402
from helpers import check_context_against_text, check_rules_against_text  # noqa: E402
from research import confidence as c
from research import evaluate, student, trials
from research.alerts import POLICIES, scan
from research.student import Scorer, Student, patient_labels, prob_policy, sha256, to_onnx, trial_features
from research import bootstrap
from research.trials import Episode, Trial, TrialError

TOY = Path(__file__).parent.parent / "tests" / "data" / "toy"
SHA = "95052d021b73da3fa71cc62601a795264b8ffd1cf5c4bf2e6e3ba407f74698a8"
CAL = list(range(301, 314))  # 13 trials x 8 stays: n = 104, so j = 1 and q is the largest drawn score
HELD = [11, 12, 13]
SEEDS = range(301, 314)  # the exact set these tests calibrate on (the command requires 301-340)


def folder(root: Path, seed: int, **manifest) -> Path:
    f = shutil.copytree(TOY, root / f"s{seed}")
    m = json.loads((f / "manifest.json").read_text()) | {"seed": seed, "days": 30, "speed": 60, "scenario_sha256": SHA} | manifest
    (f / "manifest.json").write_text(json.dumps(m))
    return f


def seed_of(f) -> int:
    return int(Path(f).name[1:])


@pytest.fixture(scope="module")
def cohort():
    return {s: trial(s) for s in CAL + HELD}


@pytest.fixture(scope="module")
def feats(cohort):
    return {s: trial_features(t) for s, t in cohort.items()}


@pytest.fixture
def opened(monkeypatch):
    log = []
    real_os, real_open = os.open, builtins.open

    def note(path):
        p = Path(str(path))
        if p.name != "" and p.parent.parent == note.root:
            log.append((p.parent.name, p.name))

    note.root = None
    monkeypatch.setattr(os, "open", lambda path, *a, **k: (note(path), real_os(path, *a, **k))[1])
    monkeypatch.setattr(builtins, "open", lambda path, *a, **k: (note(path), real_open(path, *a, **k))[1])
    return log, note


def _student(temp: bool, x: np.ndarray) -> Student:
    mean, std = student._moments(x)
    n = len(mean)
    f = np.float32
    w1 = (np.arange(n * 3, dtype=f).reshape(n, 3) % 7 - 3) / f(4)
    return Student("base", mean.astype(f), std.astype(f), ((w1, np.linspace(-1, 1, 3, dtype=f)),
                                                           (np.ones((3, 1), f) / f(2), np.array([-0.5], f))))


def write_meta(folder: Path, meta: dict) -> None:
    """metadata.json and the exports.json that vouches for it (as train_student writes them)."""
    data = json.dumps(meta).encode()
    (folder / "metadata.json").write_bytes(data)
    (folder / "exports.json").write_text(json.dumps({"metadata_sha256": sha256(data), "variants": {}}))


@pytest.fixture(scope="module")
def model(tmp_path_factory, feats):
    """A model folder like train_student writes: 'all' has a student and a threshold; 'no_temp' has
    a student but no usable threshold (so the check does not apply to it)."""
    out = tmp_path_factory.mktemp("model")
    variants = {}
    for tag, temp in (("all", True), ("no_temp", False)):
        x, _, _ = student._stack([feats[s] for s in CAL], "base", temp)
        data = to_onnx(_student(temp, x))
        sess = student.session(data)
        p = student.onnx_predict(sess, student.view(feats[11].x, temp, "base"))
        (out / c.ONNX[tag]).write_bytes(data)
        variants[tag] = {"temperature": temp, "student": {"inputs": "base"}, "onnx_sha256": sha256(data),
                         "files": {"onnx": c.ONNX[tag]},
                         "thresholds": {"student": {"threshold": float(np.median(p)) if tag == "all" else None}}}
    write_meta(out, {"framework": "MindSpore", "variants": variants})
    return out


@pytest.fixture
def patched(monkeypatch, cohort, feats):
    monkeypatch.setattr(student, "extract_all", lambda fs, cache, workers: [feats[seed_of(f)] for f in fs])
    monkeypatch.setattr(trials, "load_trial", lambda f: cohort[seed_of(f)])
    monkeypatch.setattr(evaluate, "load_trial", lambda f: cohort[seed_of(f)])
    # a tiny cohort's checked rule alerts only inside the episodes, which the evaluator's own guard refuses
    monkeypatch.setattr(evaluate, "_sanity", lambda pool, policies: None)


def folders(tmp_path, seeds):
    return [str(folder(tmp_path, s)) for s in seeds]


def test_calibration_refuses_other_seeds_having_opened_only_manifest_json(tmp_path, opened, model):
    log, note = opened
    note.root = tmp_path
    bad = {s: folder(tmp_path, s) for s in (11, 30, 61, 200, 300, 341)}
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    ok, dup = folder(tmp_path / "a", 305), folder(tmp_path / "b", 305)
    log.clear()
    for s, f in bad.items():
        with pytest.raises(TrialError, match="not in calibration seeds 301 to 340"):
            c.calibrate([str(ok), str(f)], model, tmp_path / "out")
    with pytest.raises(TrialError, match="given twice"):
        c.calibrate([str(ok), str(dup)], model, tmp_path / "out")
    other_shape = folder(tmp_path / "a", 306, days=29)
    with pytest.raises(TrialError, match="differ from ward-v2's"):
        c.calibrate([str(other_shape)], model, tmp_path / "out")
    assert {name for _, name in log} == {"manifest.json"}  # nothing else was opened
    assert not (tmp_path / "out").exists()


def test_evaluation_refuses_seeds_1_to_10_and_everything_but_11_to_30_before_reading_data(tmp_path, opened, model):
    log, note = opened
    note.root = tmp_path
    bad = [folder(tmp_path, s) for s in (1, 10, 31, 61, 305)]
    log.clear()
    for f in bad:
        with pytest.raises(TrialError, match="not in held-out ward-v2 seeds 11 to 30"):
            c.evaluate([str(f)], model, tmp_path / "out")
    assert {name for _, name in log} == {"manifest.json"} and not (tmp_path / "out").exists()


def _calibrate(tmp_path, model, name="out", **kw):
    root = tmp_path / name
    root.mkdir()
    return c.calibrate(folders(root, CAL), model, root / "model", seeds=SEEDS, **kw)


def _held(tmp_path):
    root = tmp_path / "held"
    root.mkdir()
    return folders(root, HELD)


def test_calibration_matches_an_independent_recomputation_and_is_byte_identical(tmp_path, patched, model, cohort):
    first = _calibrate(tmp_path, model, "one")
    second = _calibrate(tmp_path, model, "two")
    a, b = tmp_path / "one" / "model", tmp_path / "two" / "model"
    for name in ("calibration.json", "calibration.json.sha256"):
        assert (a / name).read_bytes() == (b / name).read_bytes()
    assert not (a / "calibration_no_temp.json").exists()
    assert any("no_temp: not applicable (the student has no usable threshold)" in line for line in first)
    cal, digest = c.load(a / "calibration.json")
    assert digest in first[-1] and first == second
    # the same numbers from the scorer path the held-out rows use (rows -> features -> ONNX -> labels)
    meta = json.loads((model / "metadata.json").read_text())["variants"]["all"]
    sess = student.session((model / "model.onnx").read_bytes())
    scorer = Scorer(lambda x: student.onnx_predict(sess, x), True, "base")
    stays, negatives = [], 0
    for seed in CAL:
        t = cohort[seed]
        by = {}
        for r in t.rows:
            by.setdefault(r.patient, {})[r.minute] = r
        for e in t.episodes:
            p = scorer(by[e.patient])
            minutes = sorted(p)
            neg = [p[m] for m, y in zip(minutes, patient_labels(minutes, e)) if y == 0]
            stays.append(neg)
            negatives += len(neg)
    assert (cal.n, cal.negative_bins, cal.seeds) == (104, negatives, CAL)
    expect = c.make(c.draw(stays), c.ALPHA, draw_seed=c.DRAW_SEED, variant="all", seeds=CAL, negative_bins=negatives,
                    t=meta["thresholds"]["student"]["threshold"], onnx_sha256=meta["onnx_sha256"], days=30, speed=60,
                    scenario_sha256=SHA)
    assert cal == expect and (cal.j, cal.k) == (1, 104) and cal.q == max(c.draw(stays))


def test_the_checked_policy_alerts_on_a_subset_and_the_held_out_block_adds_up(tmp_path, patched, model, cohort):
    _calibrate(tmp_path, model)
    out = tmp_path / "out" / "model"
    cal, _ = c.load(out / "calibration.json")
    f = _held(tmp_path)
    text = c.evaluate(f, model, out, cal_seeds=SEEDS)
    assert (out / c.HELD_OUTPUT).read_text() == text
    table = text.split("confidence check (slice M3)")[0]
    assert all(name in table.splitlines()[-30] or name in table for name in
               ("student ", "student_checked", "news2_or_student ", "news2_or_student_checked"))
    assert "student_no_temp" not in text and "no_temp: not applicable" in text
    # the subset property, bin by bin, on the same rows
    sess = student.session((model / "model.onnx").read_bytes())
    scorer = Scorer(lambda x: student.onnx_predict(sess, x), True, "base")
    s_pol, c_pol = prob_policy("s", scorer, cal.t), c.checked_policy("c", scorer, cal.t, cal.q)
    s_on = c_on = scored = 0
    for seed in HELD:
        by = {}
        for r in cohort[seed].rows:
            by.setdefault(r.patient, {})[r.minute] = r
        for rows in by.values():
            a, b = s_pol.states(rows), c_pol.states(rows)
            assert all(a[m] for m in b if b[m])
            s_on, c_on, scored = s_on + sum(a.values()), c_on + sum(b.values()), scored + len(scorer(rows))
    m = re.search(r"not sure \(p >= t and p <= q\): \S+ \((\d+)/(\d+)\) of scored bins, \S+ \((\d+)/(\d+)\) of the "
                  r"student's alerting bins", text)
    ns, sc, ns2, on = map(int, m.groups())
    assert (ns, sc, ns2, on) == (s_on - c_on, scored, s_on - c_on, s_on) and c_on < s_on
    m = re.search(r"alerting bins: student (\d+), checked (\d+), removed (-?\d+)", text)
    assert tuple(map(int, m.groups())) == (s_on, c_on, s_on - c_on)
    assert re.search(r"1\. achieved error: mean over \d+ held-out non-starting stays", text)
    assert re.search(r"verdict: (MET|NOT MET)", text) and "binding" in text
    with pytest.raises(TrialError, match="already made"):
        c.evaluate(f, model, out, cal_seeds=SEEDS)  # the held-out run is made once


def _check_block_against_text(text: str, tag: str, block: dict) -> None:
    """Everything the text block of one variant prints is in the JSON block, field by field (shares with their intervals,
    pooled ratios, d and the bound, both "4." lines, n, k, j, q, t, alpha, binding, the ONNX sha256)."""
    mine = text[text.index(f"[{tag}] confidence check"):]
    mine = mine.split("\n\n")[0]
    f4 = lambda x: f"{x:.4f}"  # noqa: E731
    m = re.search(r"calibration facts: n=(\d+) k=(\d+) \(j=(\d+)\) q=(\S+) t=(\S+) alpha=(\S+) onnx sha256 (\S+)", mine)
    assert (int(m[1]), int(m[2]), int(m[3]), float(m[4]), float(m[5]), m[6], m[7]) == (
        block["n_calibration"], block["k"], block["j"], block["q"], block["t"], block["alpha"], block["onnx_sha256"])
    assert (re.search(r"^binding$", mine, re.M) is not None) == block["binding"] and ("NOT binding" in mine) != block["binding"]
    ae, cs, ss = block["achieved_error"], block["checked_share"], block["student_share"]
    m = re.search(r"mean over (\d+) held-out .*?: (\S+), 95% stay-bootstrap interval (\S+) to (\S+), against alpha (\S+)", mine)
    assert (int(m[1]), m[2], m[3], m[4], m[5]) == (block["n_stays"], f4(ae["mean"]), f4(ae["lo"]), f4(ae["hi"]), block["alpha"])
    m = re.search(r"context: the same share for the checked rule (\S+) \((\S+) to (\S+)\), for the student alone \(p >= t\) "
                  r"(\S+) \((\S+) to (\S+)\); pooled over bins: p > q (\S+) \((\d+)/(\d+)\), checked (\S+) \((\d+)/(\d+)\), "
                  r"student (\S+) \((\d+)/(\d+)\)", mine)
    assert m.groups()[:6] == (f4(cs["mean"]), f4(cs["lo"]), f4(cs["hi"]), f4(ss["mean"]), f4(ss["lo"]), f4(ss["hi"]))
    pool = block["pooled"]
    for i, key in ((7, "above_q"), (10, "checked"), (13, "student")):
        assert (m[i], int(m[i + 1]), int(m[i + 2])) == (f4(pool[key] / pool["negative_bins"]), pool[key], pool["negative_bins"])
    m = re.search(r"verdict: (MET|NOT MET) \(rule: lower end <= alpha \+ d, d = (\S+) from", mine)
    assert m[1] == block["verdict"] and m[2] == f4(block["spread"])
    assert abs(block["bound"] - (float(block["alpha"]) + block["spread"])) < 1e-12 and block["bound"] > block["spread"]
    lines = re.findall(r"^4\. episodes \((all evaluated|not cut short)\) .*?any bin of the window (\d+); caught early .*? (\d+)$", mine, re.M)
    assert [(a, int(b), int(d)) for a, b, d in lines] == [("all evaluated", block["lost"]["all"]["any"], block["lost"]["all"]["early"]),
                                                        ("not cut short", block["lost"]["not_cut_short"]["any"], block["lost"]["not_cut_short"]["early"])]


def test_the_json_flag_holds_the_held_out_numbers_and_the_text_is_unchanged(tmp_path, patched, model, cohort):
    _calibrate(tmp_path, model)
    out, out2 = tmp_path / "out" / "model", tmp_path / "out2"
    shutil.copytree(out, out2)
    f = _held(tmp_path)
    plain = c.evaluate(f, model, out, cal_seeds=SEEDS)
    path = tmp_path / "held.json"
    text = c.evaluate(f, model, out2, cal_seeds=SEEDS, json_path=str(path))
    assert text == plain and (out2 / c.HELD_OUTPUT).read_text() == plain
    obj = json.loads(path.read_text())
    block = obj["confidence"]["all"]
    err = re.search(r"1\. achieved error: mean over (\d+) held-out non-starting stays .*?: (\S+), 95% stay-bootstrap "
                    r"interval (\S+) to (\S+), against alpha", plain)
    assert int(err[1]) == block["n_stays"] and err[2] == f"{block['achieved_error']['mean']:.4f}"
    assert (err[3], err[4]) == (f"{block['achieved_error']['lo']:.4f}", f"{block['achieved_error']['hi']:.4f}")
    assert re.search(r"verdict: (MET|NOT MET)", plain)[1] == block["verdict"]
    m = re.search(r"alerting bins: student (\d+), checked (\d+), removed (-?\d+).*new alerts: student (\d+), checked (\d+)", plain)
    assert tuple(map(int, m.groups())) == (block["alerting_bins"]["student"], block["alerting_bins"]["checked"],
                                          block["alerting_bins"]["student"] - block["alerting_bins"]["checked"],
                                          block["new_alerts"]["student"], block["new_alerts"]["checked"])
    m = re.search(r"not sure .*? \S+ \((\d+)/(\d+)\) of scored bins, \S+ \((\d+)/(\d+)\)", plain)
    ns = block["not_sure"]
    assert tuple(map(int, m.groups())) == (ns["bins"], ns["of_scored_bins"], ns["bins"], ns["of_student_alerting_bins"])
    cal, _ = c.load(out / "calibration.json")
    assert (block["q"], block["t"], block["alpha"], block["n_calibration"]) == (cal.q, cal.t, cal.alpha, cal.n)
    assert set(block["lost"]) == {"all", "not_cut_short"} and set(block["lost"]["all"]) == {"any", "early"}
    assert obj["format"] == "biosentry-held-out/1" and any(r["id"] == "student_checked" for r in obj["rules"])
    _check_block_against_text(plain, "all", block)
    assert block["student_share"]["lo"] < block["student_share"]["hi"]


def _cut_short(t: Trial, patients: tuple[str, ...]) -> Trial:
    """The stays of `patients` end shortly after their episode is fully in, before its window ends."""
    out_eps, drop = [], set()
    for e in t.episodes:
        if e.patient in patients:
            e = replace(e, leave_minute=int(e.start + e.ramp_minutes) + 30)
            drop.add((e.patient, e.leave_minute))
        out_eps.append(e)
    leave = {p: m for p, m in drop}
    rows = tuple(r for r in t.rows if r.minute <= leave.get(r.patient, 10 ** 9))
    return replace(t, rows=rows, episodes=tuple(out_eps))


def _mild(t: Trial, patients: tuple[str, ...]) -> Trial:
    """From its episode's start on, these patients have constant vitals that the test model scores 0.66: above t (0.61) and
    below q (0.76), so the student alerts and the checked rule does not (an episode the check loses)."""
    start = {e.patient: e.start for e in t.episodes}
    rows = tuple(vitals_row(r.patient, r.minute, 130, 16, 97, 36.8) if r.patient in patients and r.minute >= start[r.patient] else r
                 for r in t.rows)
    return replace(t, rows=rows)


def _burst(t: Trial, patients: tuple[str, ...], first: int, last: int) -> Trial:
    """A run of low heart rate, low temperature (the test model scores these 1.0, above the calibration's q) in stays that have no
    episode: negative bins with a supported alert, in some stays only."""
    rows = tuple(vitals_row(r.patient, r.minute, 40, 8, 97, 35.0) if r.patient in patients and first <= r.minute // 15 <= last else r
                 for r in t.rows)
    return replace(t, rows=rows)


def test_the_json_block_matches_the_text_when_episodes_are_cut_short(tmp_path, patched, model, cohort, monkeypatch):
    _calibrate(tmp_path, model)
    cut = {**cohort, **{s: _cut_short(_mild(cohort[s], ("p-000", "p-004")), ("p-000", "p-002")) for s in HELD}}
    cut[11] = _burst(cut[11], ("p-001", "p-003"), 40, 60)  # some stays, not all, have bins above q: the interval is not a point
    cut[12] = _burst(cut[12], ("p-001",), 40, 52)
    monkeypatch.setattr(trials, "load_trial", lambda f: cut[seed_of(f)])
    monkeypatch.setattr(evaluate, "load_trial", lambda f: cut[seed_of(f)])
    path = tmp_path / "cut.json"
    text = c.evaluate(_held(tmp_path), model, tmp_path / "out" / "model", cal_seeds=SEEDS, json_path=str(path))
    obj = json.loads(path.read_text())
    ctx, block = obj["context"], obj["confidence"]["all"]
    assert ctx["episodes_not_cut_short"] < ctx["episodes_evaluated"]
    _check_block_against_text(text, "all", block)
    check_context_against_text(ctx, text)
    check_rules_against_text(obj["rules"], text)
    assert block["achieved_error"]["lo"] < block["achieved_error"]["hi"]
    assert block["lost"]["all"]["any"] > block["lost"]["not_cut_short"]["any"] > 0  # the cut-short episodes add to "all" only
    assert block["lost"]["all"] != block["lost"]["not_cut_short"]


def test_an_unwritable_json_path_refuses_before_any_trial_is_read_and_writes_nothing(tmp_path, patched, model, monkeypatch):
    _calibrate(tmp_path, model)
    out = tmp_path / "out" / "model"
    f = _held(tmp_path)
    monkeypatch.setattr(evaluate, "load_trial", lambda x: pytest.fail("a trial was read"))
    monkeypatch.setattr(trials, "load_trial", lambda x: pytest.fail("a trial was read"))
    with pytest.raises(TrialError, match="--json"):
        c.evaluate(f, model, out, cal_seeds=SEEDS, json_path=str(tmp_path / "no" / "such" / "h.json"))
    assert not (out / c.HELD_OUTPUT).exists()
    assert c.main(["evaluate", *f, "--model", str(model), "--out", str(out), "--json", str(tmp_path / "no" / "h.json")]) == 2


def test_a_json_write_error_after_the_run_is_one_refusal_and_leaves_nothing_written(tmp_path, patched, model, capsys, monkeypatch):
    _calibrate(tmp_path, model)
    real = c.evaluate
    monkeypatch.setattr(c, "evaluate", lambda *a, **k: real(*a, cal_seeds=SEEDS, **k))  # the tests calibrate on 301-313
    out = tmp_path / "out" / "model"
    before = sorted(p.name for p in out.iterdir())
    target = tmp_path / "is-a-folder"
    target.mkdir()  # the early check can create a scratch file next to it; the write cannot replace a folder
    assert c.main(["evaluate", *_held(tmp_path), "--model", str(model), "--out", str(out), "--json", str(target)]) == 2
    cap = capsys.readouterr()
    assert cap.out == "" and cap.err == "refused: --json: cannot write that file\n"
    assert sorted(p.name for p in out.iterdir()) == before and not (out / c.HELD_OUTPUT).exists()
    assert sorted(p.name for p in tmp_path.iterdir() if p.name != "held") == ["is-a-folder", "out"]
    assert list(target.iterdir()) == []


def test_evaluation_refuses_a_changed_calibration_file_threshold_or_model(tmp_path, patched, model, cohort):
    _calibrate(tmp_path, model)
    out = tmp_path / "out" / "model"
    f = _held(tmp_path)
    meta = json.loads((model / "metadata.json").read_text())
    other = tmp_path / "other"
    shutil.copytree(model, other)
    # a threshold edited with exports.json left alone: refused by the file check, in both commands
    meta["variants"]["all"]["thresholds"]["student"]["threshold"] += 0.001
    (other / "metadata.json").write_text(json.dumps(meta))
    with pytest.raises(TrialError, match="metadata.json: sha256 differs from exports.json"):
        c.evaluate(f, other, out, cal_seeds=SEEDS)
    (tmp_path / "cal").mkdir()
    cal_folders = [str(folder(tmp_path / "cal", s)) for s in CAL]
    with pytest.raises(TrialError, match="metadata.json: sha256 differs from exports.json"):
        c.calibrate(cal_folders, other, tmp_path / "o2", seeds=SEEDS)
    assert not (tmp_path / "o2").exists()
    # the same edit made consistently (exports.json rewritten): the calibration's t no longer matches
    write_meta(other, meta)
    with pytest.raises(TrialError, match="threshold"):
        c.evaluate(f, other, out, cal_seeds=SEEDS)
    # a different VALID ONNX file under model.onnx: its sha256 differs from metadata.json
    shutil.copy(model / "metadata.json", other / "metadata.json")
    shutil.copy(model / "exports.json", other / "exports.json")
    shutil.copy(model / "model_no_temp.onnx", other / "model.onnx")
    student.session((other / "model.onnx").read_bytes())  # it does load
    with pytest.raises(TrialError, match="model.onnx: sha256 differs from metadata.json"):
        c.evaluate(f, other, out, cal_seeds=SEEDS)
    data = (out / "calibration.json").read_bytes()
    (out / "calibration.json").write_bytes(data + b" ")
    with pytest.raises(TrialError, match="sha256 differs"):
        c.evaluate(f, model, out, cal_seeds=SEEDS)
    (out / "calibration.json").write_bytes(data)
    with pytest.raises(TrialError, match="not made on exactly seeds 301 to 340"):
        c.evaluate(f, model, out)  # the command's default: all 40 calibration seeds
    (out / "calibration.json").unlink()
    with pytest.raises(TrialError, match="cannot be read"):
        c.evaluate(f, model, out, cal_seeds=SEEDS)
    assert not (out / c.HELD_OUTPUT).exists()  # nothing was recorded as a held-out run


def test_calibration_needs_exactly_the_given_seed_set_and_the_features_must_keep_their_seed(tmp_path, patched, model, feats, monkeypatch):
    fs = folders(tmp_path, CAL)
    with pytest.raises(TrialError, match="exactly seeds 301 to 340"):
        c.calibrate(fs, model, tmp_path / "o")  # the default is the contract's 40 seeds
    with pytest.raises(TrialError, match="exactly seeds 301 to 313"):
        c.calibrate(fs[:-1], model, tmp_path / "o", seeds=SEEDS)
    monkeypatch.setattr(student, "extract_all", lambda fs_, cache, workers: [feats[seed_of(f)] for f in fs_][::-1])
    with pytest.raises(TrialError, match="a seed changed while reading"):
        c.calibrate(fs, model, tmp_path / "o", seeds=SEEDS)
    assert not (tmp_path / "o").exists()


def test_the_command_prints_the_sha256_of_the_held_out_text(tmp_path, patched, model, capsys, monkeypatch):
    import hashlib
    _calibrate(tmp_path, model)
    out = tmp_path / "out" / "model"
    # main takes no seed parameter, so the tiny calibration set goes in as the function default
    monkeypatch.setattr(c.evaluate, "__defaults__", (c.MODEL, c.OUT, SEEDS))
    assert c.main(["evaluate", *_held(tmp_path), "--model", str(model), "--out", str(out)]) == 0
    shown = capsys.readouterr().out
    assert shown.endswith(f"{c.HELD_OUTPUT} sha256 {hashlib.sha256((out / c.HELD_OUTPUT).read_bytes()).hexdigest()}\n")


@pytest.mark.parametrize("edit", ["no_thresholds", "no_variants", "variant_not_a_dict", "no_temperature"])
def test_an_unexpected_metadata_shape_is_a_one_line_refusal_in_both_commands(tmp_path, model, edit, capsys):
    other = tmp_path / "m"
    shutil.copytree(model, other)
    meta = json.loads((model / "metadata.json").read_text())
    if edit == "no_thresholds":
        del meta["variants"]["all"]["thresholds"]
    elif edit == "no_variants":
        del meta["variants"]
    elif edit == "variant_not_a_dict":
        meta["variants"]["all"] = 7
    else:
        del meta["variants"]["all"]["temperature"]
    write_meta(other, meta)
    assert c.main(["evaluate", str(folder(tmp_path, 11)), "--model", str(other), "--out", str(tmp_path / "o")]) == 2
    assert capsys.readouterr().err.count("\n") == 1
    with pytest.raises(TrialError, match="unexpected shape"):
        c._model(other)
    assert not (tmp_path / "o").exists()


def test_a_variant_without_a_usable_threshold_or_student_is_not_applicable():
    ok = {"student": {"inputs": "base"}, "onnx_sha256": "a", "files": {"onnx": "m"}, "thresholds": {"student": {"threshold": 0.1}}}
    assert c.usable(ok) is None
    assert "no usable threshold" in c.usable(ok | {"thresholds": {"student": {"threshold": None}}})
    assert "no student" in c.usable(ok | {"student": None})
    assert "no student" in c.usable(ok | {"files": None})
    assert "no student" in c.usable(ok | {"onnx_sha256": None})


def test_the_run_log_is_deterministic_outside_time_and_has_no_path(tmp_path, patched, model):
    logs = []
    for name in ("one", "two"):
        _calibrate(tmp_path, model, name, run_log=tmp_path / name / "logs" / "log.json", run_id="m3-test.1", commit="abc1234")
        logs.append((tmp_path / name / "logs" / "log.json").read_text())
    objs = [json.loads(x) for x in logs]
    assert all("seconds" in o["time"] and set(o["time"]) == {"seconds"} for o in objs)
    stripped = [{k: v for k, v in o.items() if k != "time"} for o in objs]
    assert stripped[0] == stripped[1]
    o = stripped[0]
    assert o["model"] == "tier_c/confidence_check" and o["run_id"] == "m3-test.1" and o["commit"] == "abc1234"
    assert (o["alpha"], o["draw_seed"], o["calibration_seeds"]) == ("0.01", 20261113, CAL)
    v = o["variants"]["all"]
    assert {"n", "j", "k", "q", "t", "binding", "onnx_sha256", "negative_bins", "score_quantiles"} <= set(v)
    assert len(v["score_quantiles"]) == min(104, 200) and v["score_quantiles"] == sorted(v["score_quantiles"])
    assert set(o["packages"]) == {"numpy", "onnxruntime"} and "no_temp" in o["not_applicable"]
    text = json.dumps(stripped)
    assert str(tmp_path) not in text and not re.search(r'"/(app|tmp|Users|home|private|data)', text)
    assert not re.search(r"seconds|wall|elapsed", text)
    for bad in ({"run_id": "has space"}, {"run_id": "a/b"}, {"run_id": "x" * 65}, {"commit": "two words"}):
        with pytest.raises(TrialError):
            c.calibrate([], model, tmp_path / "z", run_log=tmp_path / "z.json", **bad)
    assert not (tmp_path / "z.json").exists()


def test_the_command_line_exits_2_with_one_line_on_a_refusal(tmp_path, capsys, model):
    f = folder(tmp_path, 11)
    assert c.main(["calibrate", str(f), "--model", str(model), "--out", str(tmp_path / "o")]) == 2
    assert capsys.readouterr().err.count("\n") == 1
    assert c.main(["evaluate", "--bogus"]) == 2 and c.main(["nope"]) == 2
    assert POLICIES[0].name == "news2_chart_trigger"


# --- the held-out block's counting, on a hand-made cohort with injected scores ----------------------
# One trial, seven stays of bins at minutes 15, 30, ... Scores are injected by a stub scorer so that the real
# counting code (`_block`, the policies, `scan`, the windows) is what runs. t = 0.5.
# Episodes (respiratory) start at 300, ramp 60: fully in 360, window 300 to 480, "early" = new alert in [300, 360).
# Bin i is at minute 15 i; 0.1 unless listed.
SCORES = {
    "P0": {i: 0.95 for i in range(1, 41)},                                  # STARTING stay, p above every q
    "E1": {21: 0.6, 22: 0.6},                                               # student fires early, checked never
    "E2": {21: 0.6, 28: 0.9},                                               # student fires early; checked fires after full in
    "E3": {i: 0.6 for i in range(10, 26)},                                  # student alert opened at 150, before the window
    "E4": {26: 0.6},                                                        # student fires after full in; stay ends at 420
    "N1": {1: 0.9, **{i: 0.6 for i in range(2, 40)}, 40: 0.9},              # no episode; removal splits one alert in two
    "N2": {i: 0.4 for i in range(1, 6)},                                    # no episode; 0.4 lies between q = 0.3 and t
}
BINS = {"P0": 40, "E1": 40, "E2": 40, "E3": 40, "E4": 27, "N1": 40, "N2": 20}


class Stub:
    _memo: dict = {}

    def __call__(self, rows):
        patient = next(iter(rows.values())).patient
        return {m: SCORES[patient].get(m // 15, 0.1) for m in rows}


def hand_trial() -> Trial:
    rows, episodes = [], []
    for name, n in BINS.items():
        rows += [vitals_row(name, 15 * i, 70) for i in range(1, n + 1)]
        episodes.append(Episode(name, 0 if name == "P0" else 15, 420 if name == "E4" else None,
                                "respiratory" if name.startswith("E") else None,
                                300.0 if name.startswith("E") else None, 60.0 if name.startswith("E") else None))
    return Trial("hand", 11, 2, 60, "0" * 64, "abcdef1", 22, 1.0, tuple(rows), tuple(episodes))


def run_block(monkeypatch, q: float) -> list[str]:
    monkeypatch.setattr(trials, "load_trial", lambda f: hand_trial())
    cal = c.Calibration(alpha=c.ALPHA, draw_seed=c.DRAW_SEED, variant="all", seeds=[301], negative_bins=0, n=104, j=1,
                        k=104, q=q, t=0.5, onnx_sha256="a" * 64, days=2, speed=60, scenario_sha256="0" * 64)
    stub = Stub()
    return c._block("all", cal, ["x"], stub, prob_policy("s", stub, 0.5), c.checked_policy("c", stub, 0.5, q))


def shares_of(*rows):
    return bootstrap.intervals({"stays": list(rows)}, {"e": lambda s: s[0] / s[3], "c": lambda s: s[1] / s[3],
                                                      "s": lambda s: s[2] / s[3]})


def test_the_block_counts_q_above_t_by_hand(monkeypatch):
    text = "\n".join(run_block(monkeypatch, 0.8))
    assert "\nbinding\n" in "\n" + text + "\n"
    # six non-starting stays with a negative bin (P0 is left out): error shares by stay (above q, checked, student)
    rows = [(0, 0, 0, 1), (0, 0, 0, 1), (0, 0, 10 / 19, 1), (0, 0, 0, 1), (2 / 40, 2 / 40, 1, 1), (0, 0, 0, 1)]
    iv = shares_of(*rows)["stays", "e"]
    err = (2 / 40) / 6
    assert re.search(rf"mean over 6 held-out non-starting stays .*with p > q: {err:.4f}, 95% stay-bootstrap interval "
                     rf"{iv[0]:.4f} to {iv[1]:.4f}, against alpha 0.01", text)
    assert f"for the student alone (p >= t) {(10 / 19 + 1) / 6:.4f}" in text
    # pooled over 19 + 19 + 19 + 19 + 40 + 20 = 136 negative bins
    assert "pooled over bins: p > q 0.0147 (2/136), checked 0.0147 (2/136), student 0.3676 (50/136)" in text
    assert "verdict: MET" in text and "NOT MET" not in text
    # student on 40 + 2 + 2 + 16 + 1 + 40 = 101 bins, 6 alerts; checked 40 + 1 + 2 = 43 bins, 4 alerts (N1 splits in two)
    assert "3. alerting bins: student 101, checked 43, removed 58 (0.574); new alerts: student 6, checked 4" in text
    assert "2. not sure (p >= t and p <= q): 0.2348 (58/247) of scored bins, 0.5743 (58/101) of the student's alerting bins" in text
    # E1, E3, E4 are lost as "alerting in any bin" (E2 is not: the checked rule alerts after full in); E1, E2 as "caught
    # early" (E3's alert opened before the window, E4's fired after full in). E4's stay ends at 420: cut short.
    assert ("4. episodes (all evaluated) detected by the student but not by the checked rule: alerting in any bin of the "
            "window 3; caught early (a new alert in the window before fully in) 2") in text
    assert ("4. episodes (not cut short) detected by the student but not by the checked rule: alerting in any bin of the "
            "window 2; caught early (a new alert in the window before fully in) 2") in text


def test_the_block_counts_q_below_t_by_hand_the_check_is_not_binding(monkeypatch):
    text = "\n".join(run_block(monkeypatch, 0.3))
    assert "NOT binding (q < t): the check removes no alert" in text
    # above q = p > 0.3: E3 10 of 19 negatives, N1 all 40, N2 5 of 20 (0.4 is above q but below t, so not "sure")
    rows = [(0, 0, 0, 1), (0, 0, 0, 1), (10 / 19, 10 / 19, 10 / 19, 1), (0, 0, 0, 1), (1, 1, 1, 1), (0.25, 0, 0, 1)]
    iv = shares_of(*rows)["stays", "e"]
    err = (10 / 19 + 1 + 0.25) / 6
    assert re.search(rf"with p > q: {err:.4f}, 95% stay-bootstrap interval {iv[0]:.4f} to {iv[1]:.4f}", text)
    assert f"for the checked rule {(10 / 19 + 1) / 6:.4f}" in text  # not the error: the two differ here
    assert "verdict: NOT MET" in text and iv[0] > 0.01 + c.spread(c.ALPHA, 104)
    assert "3. alerting bins: student 101, checked 101, removed 0 (0.000); new alerts: student 6, checked 6" in text
    assert "2. not sure (p >= t and p <= q): 0.0000 (0/247) of scored bins, 0.0000 (0/101)" in text
    assert text.count("window 0; caught early (a new alert in the window before fully in) 0") == 2


# --- held-out set 2 ---

SET2 = [1001, 1002, 1003]


def test_set_2_evaluation_has_its_own_file_guard_json_and_label(tmp_path, patched, model, cohort):
    cohort.update({s: trial(s) for s in SET2})  # the patched loaders read this module-scoped dict
    _calibrate(tmp_path, model)
    out = tmp_path / "out" / "model"
    root = tmp_path / "h2"
    root.mkdir()
    f2 = folders(root, SET2)
    path = tmp_path / "h2.json"
    text = c.evaluate(f2, model, out, cal_seeds=SEEDS, json_path=str(path), held_out_set=2)
    assert (out / c.HELD_OUTPUT_2).read_text() == text and not (out / c.HELD_OUTPUT).exists()
    obj = json.loads(path.read_text())
    assert obj["context"]["held_out_set"] == 2 and obj["context"]["held_out_seeds"] == SET2
    assert [t["seed"] for t in obj["trials"]] == SET2 and "all" in obj["confidence"]
    assert "held-out set 2, ward-v2-heldout2 seeds 1001-1400" in text and "held-out ward-v2 seeds 11-30" not in text
    assert "held-out set 2, seeds 1001 to 1400, the only ones scored: 3 of 400 trials given" in text
    with pytest.raises(TrialError, match="heldout_2.txt exists"):
        c.evaluate(f2, model, out, cal_seeds=SEEDS, held_out_set=2)  # the set-2 run is made once
    c.evaluate(_held(tmp_path), model, out, cal_seeds=SEEDS)  # set 1's guard is its own file
    assert (out / c.HELD_OUTPUT).exists()


def test_set_2_refuses_set_1_seeds_and_set_1_refuses_set_2_seeds_having_opened_only_manifest_json(tmp_path, opened, model):
    log, note = opened
    note.root = tmp_path
    one, two = folder(tmp_path, 11), folder(tmp_path, 1001)
    log.clear()
    with pytest.raises(TrialError, match="not in held-out set 2 seeds 1001 to 1400"):
        c.evaluate([str(one)], model, tmp_path / "out", held_out_set=2)
    with pytest.raises(TrialError, match="not in held-out set 2 seeds 1001 to 1400"):
        c.evaluate([str(two), str(one)], model, tmp_path / "out", held_out_set=2)
    with pytest.raises(TrialError, match="not in held-out ward-v2 seeds 11 to 30"):
        c.evaluate([str(two)], model, tmp_path / "out")
    assert {name for _, name in log} == {"manifest.json"} and not (tmp_path / "out").exists()


def test_no_calibration_or_training_loader_takes_a_set_2_seed(tmp_path, opened, model):
    log, note = opened
    note.root = tmp_path
    two = folder(tmp_path, 1001)
    log.clear()
    with pytest.raises(TrialError, match="not in calibration seeds 301 to 340"):
        c.calibrate([str(two)], model, tmp_path / "out")
    with pytest.raises(TrialError, match="seed 1001 is not used here"):
        student.load_for_training([str(two)])
    with pytest.raises(TrialError, match="seed 1001 is not used here"):
        student.load_for_evaluation([str(two)])
    assert {name for _, name in log} == {"manifest.json"} and not (tmp_path / "out").exists()


def test_the_command_passes_the_set_and_checks_it(tmp_path, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(c, "evaluate", lambda folders, model, out, **kw: seen.append(kw) or "text")
    assert c.main(["evaluate", "x", "--held-out-set", "2", "--json", "j.json"]) == 0
    assert seen[-1] == {"json_path": "j.json", "held_out_set": 2} and "heldout_2.txt sha256" in capsys.readouterr().out
    assert c.main(["evaluate", "x"]) == 0 and seen[-1] == {"json_path": None} and "heldout.txt sha256" in capsys.readouterr().out
    assert c.main(["evaluate", "x", "--held-out-set", "3"]) == 2 and len(seen) == 2


def test_the_two_set_2_commands_agree_on_every_shared_rule(tmp_path, patched, model, cohort, monkeypatch):
    from research import evaluate_student, heldout2
    cohort.update({s: trial(s) for s in SET2})
    _calibrate(tmp_path, model)
    out = tmp_path / "out" / "model"
    root = tmp_path / "h2"
    root.mkdir()
    f2 = folders(root, SET2)
    conf_json = tmp_path / "conf.json"
    c.evaluate(f2, model, out, cal_seeds=SEEDS, json_path=str(conf_json), held_out_set=2)
    # evaluate_student needs a trained MindSpore model to refit; here its student rows come from the same ONNX
    sess = student.session((model / "model.onnx").read_bytes())
    t = json.loads((model / "metadata.json").read_text())["variants"]["all"]["thresholds"]["student"]["threshold"]
    meta = json.loads((model / "metadata.json").read_text()) | {"byte_reproducible": True, "framework_version": "x"}
    mdir = tmp_path / "m"
    shutil.copytree(model, mdir)
    ms_dir = mdir
    from research.student import Scorer, onnx_predict
    scorer = Scorer(lambda x: onnx_predict(sess, x), True, "base")
    monkeypatch.setattr(evaluate_student, "student_policies", lambda train, val, m, o, scorers=None: (
        (prob_policy("student", scorer, t), prob_policy("news2_or_student", scorer, t, True)), []))
    monkeypatch.setattr(evaluate_student, "_json", lambda o, name: meta)
    monkeypatch.setattr(evaluate_student, "_check_files", lambda o: None)
    stu_json = tmp_path / "stu.json"
    evaluate_student.run(f2, out=ms_dir, result=(res := {}), held_out_set=2)
    evaluate.write_json(str(stu_json), res)
    files = [json.loads(p.read_text()) for p in (stu_json, conf_json)]
    shared = {r["id"] for r in files[0]["rules"]} & {r["id"] for r in files[1]["rules"]}
    assert {"news2_chart_trigger", "student", "news2_or_student"} <= shared
    seeds, cols, _ = heldout2.join(files)  # refuses when a shared rule has different per-trial counts
    assert seeds == SET2 and shared <= set(cols)
