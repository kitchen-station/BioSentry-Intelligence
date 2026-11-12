import pytest

from helpers import TOY, copy_toy, row, rows, trial
from research import evaluate
from research.alerts import POLICIES, fire_alerts
from research.evaluate import Pool, main, run, score_trial
from research.events import AFTER_TRIAL, OUTSIDE_STAY, STARTING, control_windows, expected_bins, windows
from research.trials import Episode, TrialError, load_trial

CHART, TOTAL5 = POLICIES


@pytest.fixture(autouse=True)
def _no_params_file(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate, "PARAMS_PATH", tmp_path / "none.json")  # the DEFAULTS rows

# Hand-computed from the toy cohort (sequences in cohort.csv). Windows: n-0001 [300, 510] fully in
# 390; n-0002 [600, 780] fully in 660. Chance control: n-0003 (admit 30, no episode) gets
# [270, 480] and [510, 690]. patient-days is set by hand to 2.5.
TOY_ALERTS = {
    "news2_chart_trigger": {"p-001": [30, 900], "n-0001": [300, 645], "n-0002": [180, 585], "n-0003": [285]},
    "news2_total_ge_5": {"p-001": [30], "n-0001": [315, 645], "n-0002": [180, 585], "n-0003": [495]},
}

# The numbers were checked against the hand computation above, except the two "95% interval"
# lines for the alert rate and new-alert sensitivity: those come from the bootstrap itself, whose
# generator is pinned by the SplitMix64 test vectors in test_bootstrap.py.
TOY_OUTPUT = open(__file__.replace("test_evaluate.py", "data/toy_expected.txt")).read()


@pytest.mark.parametrize("policy", POLICIES, ids=lambda p: p.name)
def test_toy_alerts_by_hand(policy):
    by_patient = {}
    for r in load_trial(TOY).rows:
        by_patient.setdefault(r.patient, {})[r.minute] = r
    got = {p: fire_alerts(policy, by_patient[p]) for p in by_patient}
    assert got == TOY_ALERTS[policy.name]


def test_toy_metrics_each_by_hand():
    pool = Pool()
    score_trial(load_trial(TOY), POLICIES, pool)
    assert (pool.evaluated, pool.uncut, pool.ended_early, pool.no_rows) == (2, 2, 0, 0)
    assert dict(pool.dropped) == {STARTING: 0, AFTER_TRIAL: 0, OUTSIDE_STAY: 0}
    assert (pool.controls, pool.expected_bins, pool.hypotensive) == (2, 282, 0)
    c, t = pool.tallies["news2_chart_trigger"], pool.tallies["news2_total_ge_5"]
    assert (c.alerts, t.alerts) == (7, 6)
    assert (c.outside, t.outside) == (6, 5)  # every alert except the one that counted for n-0001
    assert (c.new, c.anyb, t.new, t.anyb) == (1, 2, 1, 2)  # n-0002 is alerting from an alert opened at 585
    assert (c.leads, t.leads) == ([90.0], [75.0])
    assert (c.kinds["respiratory"], c.kinds["tachycardic"]) == ([1, 1], [0, 1])  # [new, any]
    assert (c.abstained, c.bins, c.on_bins, t.on_bins) == (3, 41, 17, 14)
    assert (c.ctrl_new, c.ctrl_any, t.ctrl_new, t.ctrl_any) == (1, 2, 0, 1)
    assert (c.start_abstained, t.start_abstained) == (0, 0)


def test_units_for_the_bootstrap_add_up_to_the_tallies():
    pool = Pool()
    score_trial(load_trial(TOY), POLICIES, pool)
    for p in POLICIES:
        alerts, evaluated, new, anyb, days = (sum(col) for col in zip(*pool.units[p.name]))
        t = pool.tallies[p.name]
        assert (alerts, evaluated, new, anyb) == (t.alerts, pool.evaluated, t.new, t.anyb)
        assert days == pytest.approx(0.5833333 + 0.6111111 + 0.7430556 + 1.0, abs=1e-6)


def test_toy_output_exact_and_repeatable(capsys):
    assert run([str(TOY)]) == TOY_OUTPUT
    assert run([str(TOY)]) == TOY_OUTPUT
    assert main([str(TOY)]) == 0
    assert capsys.readouterr().out == TOY_OUTPUT
    assert str(TOY) not in TOY_OUTPUT


def test_tuning_seed_is_loaded_but_never_scored(tmp_path):
    other = copy_toy(tmp_path / "tuning", **{"manifest.json": ('"seed": 11', '"seed": 1')})
    text = run([str(TOY), str(other)])
    assert "not scored here (the sweep tunes on them): 1\n" in text and "held-out seeds (11 to 30), the only ones scored: 11\n" in text
    assert "parity: 82 cohort rows" in text
    assert text.replace("parity: 82", "parity: 41").replace("not scored here (the sweep tunes on them): 1", "not scored here (the sweep tunes on them): none") == TOY_OUTPUT


def test_input_order_does_not_change_output(tmp_path):
    other = copy_toy(tmp_path / "b", **{"manifest.json": ('"seed": 11', '"seed": 12')})
    assert run([str(TOY), str(other)]) == run([str(other), str(TOY)])


def test_the_last_line_is_always_printed_and_no_argument_suppresses_it(capsys, tmp_path):
    assert main([str(TOY)]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == evaluate.LAST_LINE
    for flag in ["--quiet", "-q", "--no-footer", "--"]:  # there is no option parsing: a flag is a folder
        assert main([str(TOY), flag]) == 2
        out = capsys.readouterr()
        assert out.out == "" and out.err.startswith("refused: ")


# --- events -------------------------------------------------------------------------------


def _one_episode(alert_minute: int | None, *, leave: int | None = None):
    """One patient, episode start 105, ramp 60 (fully in 165, last counting minute 285)."""
    states = {15: "N0", 600: "N0"}
    if alert_minute is not None:
        states[alert_minute] = "T5"
    ep = [Episode("n-1", 15, leave, "respiratory", 105.0, 60.0)]
    return trial(list(rows("n-1", states).values()), ep)


def _tally(t, policy=CHART):
    pool = Pool()
    score_trial(t, POLICIES, pool)
    return pool, pool.tallies[policy.name]


@pytest.mark.parametrize("alert,counts,lead", [
    (90, False, None),   # one bin before the start
    (105, True, 60.0),   # at the start
    (285, True, -120.0),  # at the last counted bin: 165 + 120
    (300, False, None),  # one bin after
    (None, False, None),
])
def test_window_edges(alert, counts, lead):
    _, t = _tally(_one_episode(alert))
    assert (t.new, t.anyb, t.leads) == (int(counts), int(counts), [] if lead is None else [lead])


def test_lead_is_measured_from_the_first_new_alert_in_the_window():
    # Episode start 600, ramp 600: fully in 1200, window [600, 1320]. An early alert at 15 (outside),
    # then new alerts at 630 and at 900 (17 quiet bins between them re-arm the alert).
    ep = [Episode("n-1", 15, None, "tachycardic", 600.0, 600.0)]
    t = trial(list(rows("n-1", {15: "T5", 30: "N0", 630: "T5", 900: "T5"}).values()), ep)
    pool, tally = _tally(t)
    assert tally.alerts == 3 and tally.new == 1
    assert tally.leads == [570.0]  # 1200 - 630: not 1185 (first alert of all) and not 300 (last in window)


def test_open_alert_from_before_the_window_is_alerting_but_not_new():
    ep = [Episode("n-1", 15, None, "respiratory", 105.0, 60.0)]
    open_before = trial(list(rows("n-1", {15: "T5", 90: "T5", 105: "T5", 120: "N0"}).values()), ep)
    pool, t = _tally(open_before)
    assert (t.alerts, t.new, t.anyb, t.leads) == (1, 0, 1, [])


def test_a_policy_that_alerts_more_can_still_score_fewer_new_alerts():
    # The chart trigger is a superset of total >= 5, yet an earlier single-3 alert opens first.
    ep = [Episode("n-1", 15, None, "respiratory", 105.0, 60.0)]
    t = trial(list(rows("n-1", {15: "S3", 105: "T5"}).values()), ep)
    pool, _ = _tally(t)
    chart, total5 = (pool.tallies[p.name] for p in POLICIES)
    assert (chart.new, chart.anyb) == (0, 1) and (total5.new, total5.anyb) == (1, 1)


def test_cut_episodes_and_stays_that_end_before_fully_in():
    pool, t = _tally(_one_episode(120, leave=150))  # stay ends at 150: window [105, 285] cut, fully in 165 not reached
    assert (pool.evaluated, pool.uncut, pool.ended_early) == (1, 0, 1)
    assert (t.new, t.uncut_new, t.uncut_any) == (1, 0, 0)
    pool, t = _tally(_one_episode(120, leave=200))  # cut, but fully in at 165 was reached
    assert (pool.uncut, pool.ended_early) == (0, 0)


def test_an_episode_with_no_cohort_rows_is_its_own_count():
    ep = [Episode("n-1", 15, None, "respiratory", 105.0, 60.0)]
    pool, t = _tally(trial([], ep))
    assert (pool.evaluated, pool.no_rows, t.new) == (1, 1, 0)


@pytest.mark.parametrize("states,start,expected", [
    ({105: "NR", 120: "T5"}, 105.0, 1),   # the bin holding the start is NOT RANKED
    ({120: "T5"}, 105.0, 1),              # no row for that bin
    ({105: "T5"}, 100.0, 0),              # start 100 lies in the bin ending at 105, which is ranked
    ({105: "T5", 120: "NR"}, 106.0, 1),   # start 106 lies in the bin ending at 120
])
def test_episode_start_in_a_not_ranked_or_missing_bin(states, start, expected):
    ep = [Episode("n-1", 15, None, "respiratory", start, 60.0)]
    _, t = _tally(trial(list(rows("n-1", states).values()), ep))
    assert t.start_abstained == expected


@pytest.mark.parametrize("bp_minute,no_cuff", [(200, 0), (105, 0), (285, 0), (50, 1), (286, 1), (None, 1)])
def test_hypotensive_window_without_a_cuff_reading(bp_minute, no_cuff):
    ep = [Episode("n-1", 15, None, "hypotensive", 105.0, 60.0)]
    rs = [row("n-1", 15, "N0"), row("n-1", 120, "N0", bp_minute=bp_minute)]
    pool, _ = _tally(trial(rs, ep))
    assert (pool.hypotensive, pool.hypotensive_no_cuff) == (1, no_cuff)


def test_a_cuff_taken_in_the_window_but_first_binned_after_it_is_not_seen():
    ep = [Episode("n-1", 15, None, "hypotensive", 105.0, 60.0)]
    rs = [row("n-1", 15, "N0"), row("n-1", 300, "N0", bp_minute=284)]
    pool, _ = _tally(trial(rs, ep))
    assert (pool.hypotensive, pool.hypotensive_no_cuff) == (1, 1)


def test_expected_bins_by_hand():
    assert [expected_bins(a, b, 1) for a, b in [(0, None), (60, 900), (120, 1000), (30, 1100),
                                          (100, 110), (100, 105), (60, 60)]] == [95, 56, 59, 72, 1, 0, 0]


def test_episodes_outside_the_stay_are_dropped_and_counted():
    ep = lambda p, admit, leave, start: Episode(p, admit, leave, "tachycardic", start, 30.0)  # noqa: E731
    t = trial([], [
        ep("p-001", 0, None, 200.0),     # starting patient
        ep("a", 100, 500, 50.0),         # starts before admission
        ep("b", 100, 500, 500.0),        # starts at the minute of leaving
        ep("c", 100, 500, 900.0),        # starts after leaving
        ep("d", 100, None, 1440.0),      # starts after the trial
        ep("e", 100, None, 1400.0),      # kept; window runs past the trial end
        Episode("f", 100, None, None, None, None),  # no episode
    ])
    wins, dropped = windows(t)
    assert [w.patient for w in wins] == ["e"]
    assert dropped == {STARTING: 1, AFTER_TRIAL: 1, OUTSIDE_STAY: 3}
    assert [w.cut for w in wins] == [True]


def test_control_windows_are_defined_without_randomness():
    eps = [
        Episode("e", 15, None, "respiratory", 105.0, 60.0),   # window offset 90, length 180
        Episode("c", 30, None, None, None, None),             # control: [120, 300]
        Episode("short", 30, 200, None, None, None),          # stay ends before 300: not long enough
        Episode("start", 0, None, None, None, None),          # starting patient: no admission time
        Episode("e2", 30, None, "tachycardic", 4000.0, 30.0),  # episode starts after the trial: a control
        Episode("e3", 30, None, "tachycardic", 500.0, 30.0),   # an episode begins in the stay: evaluated, not a control
    ]
    t = trial([], eps)
    wins, _ = windows(t)
    assert control_windows(t, wins) == [
        ("c", 120.0, 300.0), ("c", 500.0, 650.0), ("e2", 120.0, 300.0), ("e2", 500.0, 650.0)]  # e and e3 are never controls
    assert control_windows(t, wins) == control_windows(t, wins)


def test_chance_control_counts_new_and_any_alerts_in_control_windows():
    eps = [Episode("e", 15, None, "respiratory", 105.0, 60.0), Episode("c", 30, None, None, None, None)]
    rs = list(rows("e", {15: "N0"}).values()) + list(rows("c", {45: "N0", 135: "T5", 150: "T5"}).values())
    pool, t = _tally(trial(rs, eps))
    assert (pool.controls, t.ctrl_new, t.ctrl_any) == (1, 1, 1)
    rs2 = list(rows("e", {15: "N0"}).values()) + list(rows("c", {45: "N0", 330: "T5"}).values())
    _, t2 = _tally(trial(rs2, eps))
    assert (t2.ctrl_new, t2.ctrl_any) == (0, 0)  # an alert after the control window does not count


def test_lead_time_median_and_iqr_by_hand():
    # Episode start 600, ramp 60, fully in 660. Alerts at 645, 630, 615, 600, 660, 675 give leads 15..-15.
    eps, rs = [], []
    for i, minute in enumerate([645, 630, 615, 600, 660, 675]):
        p = f"n-{i}"
        eps.append(Episode(p, 15, None, "tachycardic", 600.0, 60.0))
        rs += rows(p, {15: "N0", minute: "T5"}).values()
    pool = Pool()
    score_trial(trial(rs, eps), POLICIES, pool)
    assert sorted(pool.tallies["news2_chart_trigger"].leads) == [-15.0, 0.0, 15.0, 30.0, 45.0, 60.0]
    lines = evaluate._table(pool, POLICIES)
    median = next(x for x in lines if x.startswith("lead time median"))
    iqr = next(x for x in lines if x.startswith("lead time interquartile"))
    # sorted leads -15 0 15 30 45 60: median 22.5; inclusive quartiles at positions 1.25 and 3.75
    assert "22.5" in median and "3.8 to 41.2" in iqr


# --- sanity rules and refusals --------------------------------------------------------------


def test_zero_evaluated_episodes_is_an_error(tmp_path):
    only_tuning = copy_toy(tmp_path / "t1", **{"manifest.json": ('"seed": 11', '"seed": 1')})
    with pytest.raises(TrialError, match="no episodes evaluated on the held-out seeds"):
        run([str(only_tuning)])


def test_sensitivity_one_with_no_alerts_outside_windows_is_an_error():
    ep = [Episode("n-1", 15, None, "respiratory", 105.0, 60.0)]
    pool = Pool()
    score_trial(trial(list(rows("n-1", {15: "N0", 120: "T5"}).values()), ep), POLICIES, pool)
    with pytest.raises(TrialError, match="news2_chart_trigger: sensitivity 1.0 with no alerts outside"):
        evaluate._sanity(pool, POLICIES)


@pytest.mark.parametrize("old,new", [
    ('"scenario_sha256": "0', '"scenario_sha256": "1'),
    ('"days": 1,', '"days": 2,'),
    ('"speed": 60', '"speed": 30'),
])
def test_mixing_different_trials_is_refused(tmp_path, old, new):
    other = copy_toy(tmp_path / "o")
    text = (other / "manifest.json").read_text().replace('"seed": 11', '"seed": 12').replace(old, new, 1)
    (other / "manifest.json").write_text(text)
    with pytest.raises(TrialError, match="differ from the other trials"):
        run([str(TOY), str(other)])


def test_same_seed_twice_and_seed_outside_the_split_are_refused(tmp_path):
    twin = copy_toy(tmp_path / "twin")
    with pytest.raises(TrialError, match="seed 11 given twice"):
        run([str(TOY), str(twin)])
    far = copy_toy(tmp_path / "far", **{"manifest.json": ('"seed": 11', '"seed": 31')})
    with pytest.raises(TrialError, match="neither split"):
        run([str(far)])


def test_no_folders_and_refusal_exit_code(capsys, tmp_path):
    assert main([]) == 2
    assert main([str(tmp_path / "missing")]) == 2
    out = capsys.readouterr()
    assert out.out == "" and out.err.count("\n") == 2 and out.err.startswith("refused: ")


# --- params.json ----------------------------------------------------------------------------


def _with_params(monkeypatch, tmp_path, content):
    p = tmp_path / "params.json"
    if content is not None:
        p.write_bytes(content if isinstance(content, bytes) else content.encode())
    monkeypatch.setattr(evaluate, "PARAMS_PATH", p)
    return p


def test_params_file_is_read(tmp_path, monkeypatch):
    _with_params(monkeypatch, tmp_path, '{"k": 3, "p": 4}')
    text = run([str(TOY)])
    assert "baseline policies: k=3.0, p=4, from chosen on seeds 1-10 (params.json)" in text
    assert "no_temp" in text


@pytest.mark.parametrize("content, reason", [
    ("{", "not valid JSON"), ("[1]", "not a JSON object"), ('{"p": 3}', "k must"),
    ('{"k": 2}', "p must"), ('{"k": true, "p": 3}', "k must"), ('{"k": "2", "p": 3}', "k must"),
    ('{"k": 1e999, "p": 3}', "k must"), ('{"k": 0, "p": 3}', "k must"), ('{"k": 2, "p": 3.0}', "p must"),
    ('{"k": 2, "p": true}', "p must"), ('{"k": 2, "p": 0}', "p must"), ('{"k": 2, "p": 97}', "p must"),
    ("[" * 30000, "not valid JSON"), (b"{" + b" " * 70000 + b"}", "larger than"),
])
def test_bad_params_file_is_refused_in_one_line(tmp_path, monkeypatch, capsys, content, reason):
    _with_params(monkeypatch, tmp_path, content)
    assert main([str(TOY)]) == 2
    out = capsys.readouterr()
    assert out.out == "" and out.err.startswith("refused: params.json: ") and reason in out.err
    assert out.err.count("\n") == 1


def test_params_that_is_a_directory_is_refused(tmp_path, monkeypatch, capsys):
    d = tmp_path / "params.json"
    d.mkdir()
    monkeypatch.setattr(evaluate, "PARAMS_PATH", d)
    assert main([str(TOY)]) == 2 and "params.json: cannot be read" in capsys.readouterr().err


def test_params_rejected_by_the_detector_is_refused(tmp_path, monkeypatch):
    _with_params(monkeypatch, tmp_path, '{"k": 2, "p": 3}')
    monkeypatch.setattr(evaluate, "BaselineParams", lambda **kw: (_ for _ in ()).throw(ValueError("nope")))
    with pytest.raises(TrialError, match="params.json: nope"):
        run([str(TOY)])
