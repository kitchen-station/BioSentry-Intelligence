"""Held-out set 2: the set selection, the refusals, one trial at a time, the per-trial counts and the analysis.
Only the toy trial and made-up numbers; nothing here reads /data."""

import json
import weakref
from dataclasses import fields

import pytest

from helpers import TOY, copy_toy
from research import evaluate, heldout2, sweep, trials
from research.alerts import POLICIES
from research.bootstrap import SplitMix64
from research.evaluate import Pool, main, run, score_trial
from research.heldout2 import GAIN, LOSS, NONE
from research.trials import Trial, TrialError, load_trial


@pytest.fixture(autouse=True)
def _no_params_file(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate, "PARAMS_PATH", tmp_path / "none.json")


def toy_set2(root, seeds):
    root.mkdir(exist_ok=True)
    return [str(copy_toy(root / f"seed-{s}", **{"manifest.json": ('"seed": 11', f'"seed": {s}')})) for s in seeds]


# --- the per-trial counts ---------------------------------------------------------------------

def test_per_trial_counts_sum_to_the_pooled_numbers_for_every_rule(tmp_path):
    folders = toy_set2(tmp_path / "r", [1001, 1002, 1003])
    result: dict = {}
    run(folders, result=result, held_out_set=2)
    obj = json.loads(json.dumps(result))
    assert [t["seed"] for t in obj["trials"]] == [1001, 1002, 1003]
    for r in obj["rules"]:
        rows = [t["rules"][r["id"]] for t in obj["trials"]]
        total = lambda k: sum(x[k] for x in rows)  # noqa: E731
        assert total("shifts") == pytest.approx(obj["context"]["patient_days"] * 2)
        assert total("alerts") == pytest.approx(r["per_shift"]["value"] * total("shifts"))
        assert (total("bins_alerting"), total("bins")) == (r["time_in_alert"]["n"], r["time_in_alert"]["d"])
        assert (total("new_alert"), total("episodes")) == (r["new_alert"]["n"], r["new_alert"]["d"])
        assert (total("any_bin"), total("episodes")) == (r["any_bin"]["n"], r["any_bin"]["d"])
        assert (total("any_bin_not_cut_short"), total("episodes_not_cut_short")) == (
            r["any_bin_not_cut_short"]["n"], r["any_bin_not_cut_short"]["d"])
        assert (total("new_alert_not_cut_short"), total("episodes_not_cut_short")) == (
            r["new_alert_not_cut_short"]["n"], r["new_alert_not_cut_short"]["d"])
        assert (total("chance_new"), total("control_windows")) == (r["chance_new"]["n"], r["chance_new"]["d"])
        assert (total("chance_any"), total("control_windows")) == (r["chance_any"]["n"], r["chance_any"]["d"])
        assert total("episodes") == obj["context"]["episodes_evaluated"]
        assert total("episodes_not_cut_short") == obj["context"]["episodes_not_cut_short"]
        assert total("control_windows") == obj["context"]["control_windows"]
        assert sum(x["kinds"]["respiratory"]["any_bin"] for x in rows) + sum(x["kinds"]["tachycardic"]["any_bin"] for x in rows) \
            + sum(x["kinds"]["hypotensive"]["any_bin"] for x in rows) == total("any_bin")
        assert set(rows[0]) >= {"alerts", "shifts", "bins", "bins_alerting", "episodes", "any_bin", "new_alert",
                                "episodes_not_cut_short", "any_bin_not_cut_short", "new_alert_not_cut_short",
                                "control_windows", "chance_any", "chance_new", "kinds"}


def test_per_trial_counts_equal_the_tallies_of_one_pool():
    pool = Pool()
    for _ in range(3):
        score_trial(load_trial(TOY), POLICIES, pool)
    assert len(pool.per_trial) == 3
    for p in POLICIES:
        t = pool.tallies[p.name]
        mine = [x["rules"][p.name] for x in pool.per_trial]
        assert [sum(m[k] for m in mine) for k in ("alerts", "bins", "bins_alerting", "new_alert", "any_bin", "chance_any")] \
            == [t.alerts, t.bins, t.on_bins, t.new, t.anyb, t.ctrl_any]
        assert mine[0] == mine[1] == mine[2]  # the same trial three times


def test_set_1_json_keeps_its_keys_and_adds_only_trials(tmp_path):
    a: dict = {}
    run([str(TOY)], result=a)
    assert set(a) == {"format", "context", "rules", "trials"} and "held_out_set" not in a["context"]
    assert a["trials"][0]["seed"] == 11


# --- set selection and the refusals -----------------------------------------------------------

def test_set_1_text_is_unchanged_with_the_flag_spelled_out(capsys):
    expected = open(str(TOY).replace("toy", "toy_expected.txt")).read()
    assert main([str(TOY), "--held-out-set", "1"]) == 0 and capsys.readouterr().out == expected
    assert run([str(TOY)], held_out_set=1) == expected


def test_set_2_header_and_context(tmp_path):
    folders = toy_set2(tmp_path / "r", [1001, 1002])
    result: dict = {}
    text = run(folders, result=result, held_out_set=2)
    assert "held-out set 2, seeds 1001 to 1400, the only ones scored: 2 of 400 trials given (1001, 1002)\n" in text
    assert "tuning seeds: none (held-out set 2 takes no tuning seed)\n" in text
    assert "held-out seeds (11 to 30)" not in text
    assert result["context"]["held_out_set"] == 2 and result["context"]["held_out_seeds"] == [1001, 1002]


def test_set_2_with_all_400_trials_says_so_compactly(tmp_path, monkeypatch):
    folders = toy_set2(tmp_path / "r", range(1001, 1401))
    monkeypatch.setattr(evaluate, "_sanity", lambda *a: None)
    text = run(folders, held_out_set=2)
    assert "held-out set 2, seeds 1001 to 1400, the only ones scored: 400 trials\n" in text


def _manifest_only(root, seed):
    d = root / f"seed-{seed}"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"seed": seed}))
    return str(d)


@pytest.mark.parametrize("seeds, reason", [
    ([1001, 11], "not in held-out set 2"),  # mixing the sets
    ([1001, 5], "mixes held-out sets"),
    ([1001, 1401], "not in held-out set 2"),
    ([1000], "not in held-out set 2"),
    ([1001, 1001], "given twice"),
])
def test_set_2_refusals_open_only_manifests(tmp_path, monkeypatch, capsys, seeds, reason):
    folders = [_manifest_only(tmp_path / str(i), s) for i, s in enumerate(seeds)]
    opened = []
    real = trials._read
    monkeypatch.setattr(trials, "_read", lambda folder, name, cap: opened.append(name) or real(folder, name, cap))
    assert main([*folders, "--held-out-set", "2"]) == 2
    err = capsys.readouterr().err
    assert reason in err and err.count("\n") == 1
    assert set(opened) == {"manifest.json"}  # no cohort file, no episodes file


def test_set_1_refuses_a_set_2_seed_and_set_2_refuses_a_set_1_folder(tmp_path):
    (one,) = toy_set2(tmp_path / "r", [1001])
    with pytest.raises(TrialError, match="neither split"):
        run([one])
    with pytest.raises(TrialError, match="not in held-out set 2"):
        run([str(TOY)], held_out_set=2)


def test_the_flag_is_checked(tmp_path, capsys):
    for bad in (["--held-out-set", "3"], ["--held-out-set"], ["--held-out-set", "1", "--held-out-set", "2"]):
        assert main([str(TOY), *bad]) == 2
        assert capsys.readouterr().err.startswith("refused:")


def test_sweep_refuses_a_set_2_manifest_before_opening_anything_else(tmp_path, monkeypatch):
    folder = _manifest_only(tmp_path, 1001)
    opened = []
    real = trials._read
    monkeypatch.setattr(trials, "_read", lambda folder, name, cap: opened.append(name) or real(folder, name, cap))
    with pytest.raises(TrialError, match="neither split"):
        sweep.sweep([folder])
    assert set(opened) == {"manifest.json"}


# --- one trial in memory at a time -------------------------------------------------------------

class Traced(Trial):
    """A Trial that can be weakly referenced, so a test can see when it was released."""


def test_one_trial_is_alive_at_a_time_in_both_sets(tmp_path, monkeypatch):
    real = evaluate.load_trial
    refs: list = []

    def spy(folder):
        assert all(r() is None for r in refs), "a second trial was loaded before the first was released"
        t = real(folder)
        traced = Traced(*[getattr(t, f.name) for f in fields(t)])
        refs.append(weakref.ref(traced))
        return traced

    monkeypatch.setattr(evaluate, "load_trial", spy)
    released = []
    run(toy_set2(tmp_path / "a", [1001, 1002, 1003, 1004]), held_out_set=2, release=lambda: released.append(len(refs)))
    assert len(refs) == 4 and released == [1, 2, 3, 4]
    refs.clear()
    other = [str(copy_toy(tmp_path / f"b{s}", **{"manifest.json": ('"seed": 11', f'"seed": {s}')})) for s in (11, 12, 13)]
    run(other)
    assert len(refs) == 3


# --- the analysis -----------------------------------------------------------------------------

def counts(episodes=100, any_bin=50, chance_any=10, control=100, alerts=100, shifts=10.0):
    return {"alerts": alerts, "shifts": shifts, "bins": 1000, "bins_alerting": 100, "episodes": episodes,
            "any_bin": any_bin, "new_alert": any_bin, "episodes_not_cut_short": episodes,
            "any_bin_not_cut_short": any_bin, "new_alert_not_cut_short": any_bin, "control_windows": control,
            "chance_any": chance_any, "chance_new": chance_any,
            "kinds": {"respiratory": {"evaluated": 4, "any_bin": 2}, "tachycardic": {"evaluated": 3, "any_bin": 1},
                      "hypotensive": {"evaluated": 3, "any_bin": 1}}}


def make_file(per_rule: dict, n=5, seeds=None, held_out_set=2):
    """per_rule: {id: counts() dict (the same in every trial) or a list of n dicts}."""
    seeds = seeds or list(range(1001, 1001 + n))
    one = lambda v, i: v[i] if isinstance(v, list) else v  # noqa: E731
    return {"format": "biosentry-held-out/1",
            "context": {"held_out_set": held_out_set, "trials": n, "held_out_seeds": seeds},
            "rules": [{"id": r, "lead": {"n": 3, "median": 30.0, "q1": 1, "q3": 2}} for r in per_rule],
            "trials": [{"seed": s, "patient_days": 1.0, "rules": {r: one(v, i) for r, v in per_rule.items()}}
                       for i, s in enumerate(seeds)]}


def three(x_any, n_any=500, x_alerts=150, n_alerts=100, chance=100, episodes=1000):
    base = counts(episodes, n_any, chance, 1000, n_alerts)
    x = counts(episodes, x_any, chance, 1000, x_alerts)
    return {"news2_chart_trigger": base, "news2_or_student": x, "news2_or_student_checked": x, "news2_or_reward_final": x}


def test_hand_computed_gain_and_alert_ratio():
    # NEWS2: 500/1000 - 100/1000 = 0.4; X: 600/1000 - 100/1000 = 0.5; gain 0.1; alerts 150 / 100 = 1.5
    obj, text = heldout2.analyse([make_file(three(600))])
    p = obj["primary"][0]
    assert p["gain"]["estimate"] == pytest.approx(0.1) and p["alert_ratio"]["estimate"] == pytest.approx(1.5)
    assert p["gain"]["ci_95"] == pytest.approx([0.1, 0.1]) and p["gain"]["ci_98.33"] == pytest.approx([0.1, 0.1])
    assert p["verdict"] == GAIN and "verdict: a gain beyond chance is shown" in text


def test_verdict_is_strict_just_above_exactly_and_just_below_zero():
    for x_any, word in ((501, GAIN), (500, NONE), (499, LOSS)):  # 5 identical trials: the interval is one point
        obj, text = heldout2.analyse([make_file(three(x_any))])
        assert obj["primary"][0]["verdict"] == word and f"verdict: {word}" in text
    assert heldout2.verdict((0.0, 1.0)) == NONE and heldout2.verdict((-1.0, 0.0)) == NONE
    assert heldout2.verdict((1e-12, 1.0)) == GAIN and heldout2.verdict((-1.0, -1e-12)) == LOSS
    assert heldout2.verdict(None) == NONE


def test_the_verdict_uses_the_98_33_interval_not_the_95():
    vals = list(range(-30, 1970))  # lower ends: 95% at index 50 (value 20), 98.33% at index 16 (value -14)
    assert heldout2.percentile(vals, heldout2.TAILS["95"])[0] == 20
    assert heldout2.percentile(vals, heldout2.TAILS["98.33"])[0] == -14
    assert heldout2.verdict(heldout2.percentile(vals, heldout2.TAILS["95"])) == GAIN
    assert heldout2.verdict(heldout2.percentile(vals, heldout2.TAILS["98.33"])) == NONE


def test_the_percentile_index_rule():
    v = list(range(2000))
    assert heldout2.percentile(v[::-1], heldout2.TAILS["95"]) == (50, 1949)
    assert heldout2.percentile(v, heldout2.TAILS["98.33"]) == (16, 1982)
    assert heldout2.percentile([5.0], heldout2.TAILS["95"]) == (5.0, 5.0) and heldout2.percentile([], heldout2.TAILS["95"]) is None
    assert float(heldout2.TAILS["98.33"]) == pytest.approx(0.05 / 3 / 2)


def test_the_bootstrap_resamples_trials_with_the_documented_generator():
    col = {("a", "x"): [1, 2, 3, 4, 5, 6], ("a", "y"): [10, 10, 10, 10, 10, 10]}
    stats = {"m": lambda s: s["a", "x"] / s["a", "y"]}
    got = heldout2.bootstrap(col, stats, 6, resamples=40, seed=7)
    rng, vals = SplitMix64(7), []
    for _ in range(40):
        idx = [rng.next() % 6 for _ in range(6)]
        vals.append(sum(col["a", "x"][i] for i in idx) / 60)
    assert got["m"]["estimate"] == 21 / 60
    assert got["m"]["95"] == heldout2.percentile(vals, heldout2.TAILS["95"])
    assert got["m"]["98.33"] == heldout2.percentile(vals, heldout2.TAILS["98.33"])
    assert (heldout2.SEED, heldout2.RESAMPLES) == (20261114, 2000)


def test_trials_are_the_unit_and_ratios_are_recomputed_from_sums():
    per = [counts(episodes=10 * (i + 1), any_bin=i + 1, chance_any=0, control=10) for i in range(5)]
    f = make_file({"news2_chart_trigger": per, "news2_or_student": per, "news2_or_student_checked": per,
                   "news2_or_reward_final": per})
    obj, _ = heldout2.analyse([f], resamples=50)
    assert obj["primary"][0]["gain"]["estimate"] == 0.0 and obj["primary"][0]["verdict"] == NONE
    # a different count in one trial changes the estimate by the pooled ratio, not by a mean of trial ratios
    x = [dict(c) for c in per]
    x[0]["any_bin_not_cut_short"] += 5
    f = make_file({"news2_chart_trigger": per, "news2_or_student": x, "news2_or_student_checked": x,
                   "news2_or_reward_final": x})
    obj, _ = heldout2.analyse([f], resamples=50)
    assert obj["primary"][0]["gain"]["estimate"] == pytest.approx(5 / 150)


def test_deterministic_bytes_and_files(tmp_path, capsys):
    paths = []
    for name, rules in (("a", three(600)), ("b", {"news2_chart_trigger": three(600)["news2_chart_trigger"],
                                                  "student": counts(alerts=120, any_bin=55)})):
        p = tmp_path / f"{name}.json"
        p.write_text(json.dumps(make_file(rules)))
        paths.append(str(p))
    outs = []
    for i in (1, 2):
        assert heldout2.main(["analyse", *paths, "--out", str(tmp_path / f"o{i}.json")]) == 0
        outs.append(capsys.readouterr().out)
    assert outs[0] == outs[1] and (tmp_path / "o1.json").read_bytes() == (tmp_path / "o2.json").read_bytes()
    assert str(tmp_path) not in outs[0]
    obj = json.loads((tmp_path / "o1.json").read_text())
    assert (tmp_path / "o1.json").read_text() == json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    assert obj["secondary_alone"][0]["id"] == "student"
    sec = outs[0].split("SECONDARY (no verdict word)")[1]
    assert "verdict" not in sec and "gain beyond chance is shown" not in sec


def test_refusals(tmp_path, capsys):
    good = make_file(three(600))
    for bad, text in ((make_file(three(600), held_out_set=1), "not from held-out set 2"),
                      ({**good, "trials": []}, "no per-trial counts"),
                      (make_file(three(600), seeds=[1001, 1002, 1003, 1004, 1006]), "disagree"),
                      (make_file({"news2_chart_trigger": counts()}), "needed for the primary comparisons are missing: "
                       "news2_or_student, news2_or_student_checked, news2_or_reward_final")):
        with pytest.raises(TrialError, match=text):
            heldout2.analyse([good, bad] if "disagree" in text else [bad])
    other = make_file({"news2_chart_trigger": counts(any_bin=51)})
    with pytest.raises(TrialError, match="different per-trial counts"):
        heldout2.analyse([good, other])
    same = make_file({"news2_chart_trigger": three(600)["news2_chart_trigger"]})
    assert heldout2.analyse([good, same])[0]["trials"] == 5  # the same rule twice with the same counts is kept once
    p = tmp_path / "set1.json"
    p.write_text(json.dumps(make_file(three(600), held_out_set=1)))
    out = tmp_path / "o.json"
    assert heldout2.main(["analyse", str(p), "--out", str(out)]) == 2 and not out.exists()
    assert "not from held-out set 2" in capsys.readouterr().err
    assert heldout2.main(["analyse", str(p)]) == 2 and heldout2.main(["other"]) == 2
    assert heldout2.main(["analyse", str(tmp_path / "missing.json"), "--out", str(out)]) == 2


def test_the_analysis_runs_on_the_json_the_evaluation_writes(tmp_path):
    folders = toy_set2(tmp_path / "r", [1001, 1002, 1003])
    result: dict = {}
    run(folders, result=result, held_out_set=2)
    obj = json.loads(json.dumps(result))
    alias = {"news2_or_student": "news2_total_ge_5", "news2_or_student_checked": "news2_total_ge_5",
             "news2_or_reward_final": "news2_total_ge_5"}
    obj["rules"] += [{**next(r for r in obj["rules"] if r["id"] == src), "id": new} for new, src in alias.items()]
    for t in obj["trials"]:
        for new, src in alias.items():
            t["rules"][new] = t["rules"][src]
    out, text = heldout2.analyse([obj], resamples=100)
    assert out["trials"] == 3 and out["seeds"] == [1001, 1002, 1003]
