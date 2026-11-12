import json
from fractions import Fraction

import pytest

from helpers import TOY, copy_toy
from research import sweep
from research.sweep import Setting, choose
from research.trials import TrialError


def S(k, p, alerts, excess):
    return Setting(k, p, alerts, Fraction(excess))


def test_choose_best_excess_inside_the_budget():
    grid = [S(2.0, 3, 120, "1/2"), S(3.0, 3, 125, "1/4"), S(1.5, 2, 126, "9/10")]  # budget 100 * 1.25
    assert choose(grid, 100) == S(2.0, 3, 120, "1/2")  # 126 is over; 125 is exactly on the limit


def test_choose_budget_edge_is_inclusive_and_exact():
    assert choose([S(2.0, 3, 125, "1/2")], 100) is not None
    assert choose([S(2.0, 3, 126, "1/2")], 100) is None


def test_choose_tie_breaks_fewer_alerts_then_larger_k_then_larger_p():
    assert choose([S(2.0, 3, 110, "1/2"), S(3.0, 8, 105, "1/2")], 100).alerts == 105
    assert choose([S(2.0, 8, 105, "1/2"), S(3.0, 3, 105, "1/2")], 100).k == 3.0
    assert choose([S(3.0, 3, 105, "1/2"), S(3.0, 8, 105, "1/2")], 100).p == 8


def test_choose_nothing_when_no_setting_is_within_budget():
    assert choose([S(2.0, 3, 200, "1")], 100) is None and choose([], 100) is None


def test_held_out_seeds_are_never_scored(tmp_path, monkeypatch):
    tune = copy_toy(tmp_path / "t", **{"manifest.json": ('"seed": 11', '"seed": 3')})
    held = copy_toy(tmp_path / "h", **{"manifest.json": ('"seed": 11', '"seed": 12')})
    seen = []
    real = sweep.score_trial
    monkeypatch.setattr(sweep, "score_trial", lambda t, *a: (seen.append(t.seed), real(t, *a))[1])
    with_held = sweep.sweep([str(tune), str(held)])
    assert seen == [3]
    assert json.dumps(with_held[0]) == json.dumps(sweep.sweep([str(tune)])[0])
    with pytest.raises(TrialError, match="no tuning trial"):
        sweep.sweep([str(held)])
    with pytest.raises(TrialError, match="no tuning trial"):
        sweep.sweep([str(TOY)])  # seed 11


def test_main_writes_nothing_without_the_flag_and_nothing_when_none_chosen(tmp_path, capsys):
    tune = copy_toy(tmp_path / "t", **{"manifest.json": ('"seed": 11', '"seed": 3')})
    out = tmp_path / "params.json"
    assert sweep.main([str(tune)]) == 0 and not out.exists()
    text = capsys.readouterr().out
    assert "chosen:" in text and text.splitlines()[-1].startswith("synthetic, not validated")
    assert sweep.main([str(tune), "--write-params", str(out)]) == 0
    assert out.exists() == ("chosen: k=" in capsys.readouterr().out)
    assert sweep.main([str(TOY)]) == 2


def test_sweep_excess_is_any_bin_minus_chance(tmp_path):
    tune = copy_toy(tmp_path / "t", **{"manifest.json": ('"seed": 11', '"seed": 3')})
    rows, _, ref = sweep.sweep([str(tune)])
    c = ref["news2_chart_trigger"]
    assert c["chance_any"][0] > 0
    for o in (c, rows[0]["news2_or_baseline"], rows[0]["baseline_alone"]):
        want = o["any_uncut"][0] / o["any_uncut"][1] - o["chance_any"][0] / o["chance_any"][1]
        assert o["excess_any_uncut"] == pytest.approx(want, abs=1e-4)


def _fake(monkeypatch, best):
    monkeypatch.setattr(sweep, "sweep", lambda f: ([], best, {"trials": 0, "episodes_evaluated": 0, "not_cut_short": 0,
        "control_windows": 0, "news2_chart_trigger": {"alerts": 0, "alerts_per_patient_12h": 0, "any_uncut": [0, 0],
        "chance_any": [0, 0], "excess_any_uncut": 0}}))


def test_write_params_is_atomic_and_exits_1_with_nothing_chosen(tmp_path, monkeypatch, capsys):
    out = tmp_path / "params.json"
    _fake(monkeypatch, None)
    assert sweep.main(["x", "--write-params", str(out)]) == 1
    assert "chosen: none" in capsys.readouterr().out and not out.exists() and list(tmp_path.iterdir()) == []
    _fake(monkeypatch, S(3.0, 4, 1, "0"))
    assert sweep.main(["x", "--write-params", str(out)]) == 0
    assert json.loads(out.read_text())["p"] == 4 and [p.name for p in tmp_path.iterdir()] == ["params.json"]


def test_write_params_failure_is_one_refused_line(tmp_path, monkeypatch, capsys):
    _fake(monkeypatch, S(3.0, 4, 1, "0"))
    for bad in (tmp_path, tmp_path / "missing" / "p.json"):  # a directory; an unwritable place
        assert sweep.main(["x", "--write-params", str(bad)]) == 2
        err = capsys.readouterr().err
        assert err == "refused: cannot write the params file\n"


def test_excess_subtracts_chance_so_the_choice_can_flip():
    # same any-bin rate (40/50), different chance: only the lower-chance setting has more excess
    a, b = sweep.excess(40, 50, 5, 10), sweep.excess(40, 50, 1, 10)
    assert (a, b) == (Fraction(3, 10), Fraction(7, 10))
    assert choose([Setting(2.0, 3, 100, a), Setting(3.0, 4, 100, b)], 100).k == 3.0
    assert choose([Setting(3.0, 4, 100, a), Setting(2.0, 3, 100, b)], 100).k == 2.0
