"""Slice M2: the reward loop. Tiny made-up cohorts only; every size is a parameter of the code."""

import json
import shutil
from types import SimpleNamespace

import pytest

from research import evaluate, reward_loop, trials
from research.bootstrap import SplitMix64
from research.reward_loop import (DEFAULT_CONFIG, MENU, NeedSeeds, Plan, Runner, T_CRIT, advance, confirm_test,
                                  generation_seeds, load_seed, main, parse_change, read_ledger, sample,
                                  sigma_start, start_params, step)
from research.reward_policy import Params, Score, generation_zero, prepare
from research.trials import TrialError
from test_reward import _write_folder, toy

CFG = {**DEFAULT_CONFIG, "selection_trials": 2}
PLAN = Plan(val_seeds=range(101, 104), first_seed=111, last_seed=210, confirm=3, candidates=6,
            max_generations=4, patience=3, max_rounds=4, base=CFG)
_PREP: dict = {}


def prep(seed: int):
    if seed not in _PREP:
        _PREP[seed] = prepare(toy(seed))
    return _PREP[seed]


class MemSource:
    def __init__(self, missing=()):
        self.missing = set(missing)

    def check(self, seeds):
        if any(s in self.missing for s in seeds):
            raise NeedSeeds(min(seeds), max(seeds))

    def prepared(self, seeds, runner):
        return [prep(s) for s in seeds]


def go(out, argv, src=None, plan=PLAN):
    return main(["--out", str(out), *argv], plan=plan, source=src or MemSource())


def tree(out) -> dict:
    return {str(p.relative_to(out)): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


def recs(out, rnd=1):
    d = out / "rounds" / f"round-{rnd}"
    return [json.loads(p.read_text()) for p in sorted(d.glob("gen-*.json"))]


def reseal(path, edit):
    rec = json.loads(path.read_text())
    edit(rec)
    body = {k: v for k, v in rec.items() if k != "sha256"}
    path.write_bytes(reward_loop._dump({**body, "sha256": reward_loop.digest(body)}))


@pytest.fixture(scope="module")
def finished(tmp_path_factory):
    """Round 1, the stop line and the no-temperature run, on the toy cohort."""
    out = tmp_path_factory.mktemp("out")
    assert go(out, ["--round", "1"]) == 0
    assert go(out, ["--stop", "one round is enough here"]) == 0
    assert go(out, ["--no-temperature"]) == 0
    return out


def fake_scores(monkeypatch, *grids):
    """Replace score_grid: each call returns the next grid of rewards (rows = policies)."""
    calls = []
    it = iter(grids)

    def fake(params, trials_, form, runner):
        calls.append((len(params), len(trials_), form))
        rows = next(it)
        return [[Score(r, 0, 0.0, 0.0, bad) for r, bad in (x if isinstance(x[0], tuple) else [(v, False) for v in x])]
                for x in rows]

    monkeypatch.setattr(reward_loop, "score_grid", fake)
    return calls


def run_step(cfg=CFG, sigma=None, sel=2, conf=3):
    cur = generation_zero()
    sel_t = [SimpleNamespace(seed=s) for s in range(sel)]
    conf_t = [SimpleNamespace(seed=s) for s in range(10, 10 + conf)]
    plan = Plan(candidates=4, confirm=conf)
    return cur, reward_loop.step(cur, sigma or sigma_start(cfg), cfg, frozenset(), plan, 1, 1, sel_t, conf_t, Runner())


# --- the confirmation rule ---------------------------------------------------------------------

def spread(mean: float) -> list[float]:
    return [mean + 1.0] * 6 + [mean - 1.0] * 6  # sd = sqrt(12 / 11); threshold 1.796 x sd / sqrt(12) = 0.54151


def test_confirmation_rule_on_hand_computed_numbers():
    assert T_CRIT[12] == 1.796
    ok, mean, sd, thr = confirm_test(spread(0.5425))
    assert (round(mean, 6), round(sd, 6), round(thr, 5)) == (0.5425, 1.044466, 0.54151) and ok
    assert not confirm_test(spread(0.5405))[0]  # just under: rejected
    assert not confirm_test([0.0] * 12)[0]  # stdev 0 and mean 0: strict, so rejected
    assert confirm_test([0.2] * 12)[0] and confirm_test([0.2] * 12)[3] == 0.0  # stdev 0: mean(d) > 0
    assert not confirm_test([-0.1] * 12)[0]
    d = [0.5, 0.3, 0.7, 0.4, 0.6, 0.5, 0.5, 0.4, 0.6, 0.5, 0.5, 0.5]  # mean 0.5, sd 0.104447 (n - 1)
    assert round(confirm_test(d)[2], 6) == 0.104447


def test_a_winner_that_fails_is_never_replaced_by_the_second_best(monkeypatch):
    n = 2
    calls = fake_scores(monkeypatch, [[0.0] * n, [1.0] * n, [0.9] * n, [0.1] * n, [0.1] * n, [0.1] * n, [0.1] * n],
                        [[0.0] * 3, [0.0] * 3])  # the winner's confirmation d is all 0: refused
    cur, (fields, new, _) = run_step()
    assert not fields["accepted"] and new == cur and len(calls) == 2  # no third look at the batch
    assert fields["confirmation"]["mean"] == 0.0 and fields["reason"] == "mean(d) not above the threshold"
    assert fields["selection"]["winner"]["mean"] == 1.0


def test_selection_rules(monkeypatch):
    n = 2
    # all equal to the current: nothing above it, no confirmation scored
    calls = fake_scores(monkeypatch, [[0.5] * n] * 5)
    _, (fields, _, sig) = run_step()
    assert not fields["accepted"] and len(calls) == 1 and fields["confirmation"] is None
    assert sig["w"] == 0.5 * 0.85
    # the best candidate is rejected for time in alert: dropped; the next best is the winner
    bad = [(9.0, True), (9.0, False)]
    calls = fake_scores(monkeypatch, [[0.0] * n, bad, [1.0] * n, [1.0] * n, [0.2] * n, [0.2] * n],
                        [[0.0, 0.0, 0.0], [1.0, 1.0, 1.1]])
    cur, (fields, new, sig) = run_step()
    assert fields["selection"]["candidates_rejected"] == 1 and fields["accepted"] and new != cur
    assert fields["selection"]["winner"]["mean"] == 1.0 and sig == sigma_start(CFG)  # kept after a success
    # a tie goes to the first drawn
    fake_scores(monkeypatch, [[0.0] * n, [1.0] * n, [1.0] * n, [0.0] * n, [0.0] * n, [0.0] * n], [[0.0] * 3, [1.0] * 3])
    rng = SplitMix64(reward_loop.SEED + 1001)
    first = sample(generation_zero(), sigma_start(CFG), rng, CFG, frozenset())
    assert run_step()[1][1] == first
    # everything rejected: nothing accepted, no confirmation
    calls = fake_scores(monkeypatch, [[0.0] * n, *[[(5.0, True)] * n] * 4])
    assert run_step()[1][0]["reason"].startswith("no candidate left") and len(calls) == 1


def test_a_winner_rejected_on_a_confirmation_seed_is_not_accepted(monkeypatch):
    n = 2
    fake_scores(monkeypatch, [[0.0] * n, [1.0] * n, *[[0.0] * n] * 3], [[0.0, 0.0, 0.0], [(1.0, False), (1.0, True), (1.0, False)]])
    _, (fields, _, _) = run_step()
    assert not fields["accepted"] and fields["confirmation"]["winner_rejected"]
    assert fields["reason"].startswith("winner rejected")


def test_current_policys_own_rejection_neither_blocks_nor_forces(monkeypatch):
    n = 2
    cur_bad = [(0.0, True)] * n
    fake_scores(monkeypatch, [cur_bad, [1.0] * n, *[[0.0] * n] * 3], [[(0.0, True)] * 3, [1.0] * 3])
    _, (fields, _, _) = run_step()
    assert fields["accepted"] and fields["selection"]["current_rejected"] and fields["confirmation"]["current_rejected"]


# --- patience, cap, sigma ----------------------------------------------------------------------

def test_patience_resets_after_an_improvement_and_stops_at_three():
    plan = Plan()
    st = (None, None, 0)
    seq = []
    for g, r in enumerate([0.5, 0.5, 0.4, 0.6, 0.6, 0.6, 0.6], 1):
        *st, stopped = advance(*st, g, r, plan)
        seq.append((st[2], stopped))
    assert seq == [(0, None), (1, None), (2, None), (0, None), (1, None), (2, None), (3, "patience")]
    assert advance(0.5, 0, 2, 5, None, plan)[2:] == (3, "patience")  # a rejected generation does not improve
    assert advance(None, None, 0, 1, 0.1, plan)[:2] == (0.1, 1)  # any non-rejected value beats "rejected"
    assert advance(0.5, 1, 0, 39, 0.5, plan)[3] is None and advance(0.5, 1, 0, 40, 0.6, plan)[3] == "max_generations"


def test_seed_schedule_values():
    assert generation_seeds(401, 8, 12, 1) == (list(range(401, 409)), list(range(409, 421)), 421)
    assert generation_seeds(401, 8, 12, 2) == (list(range(421, 429)), list(range(429, 441)), 441)
    assert generation_seeds(1201, 16, 12, 3)[2] == 1201 + 28 * 3
    seen = []
    for g in range(1, 41):
        s, c, _ = generation_seeds(401, 8, 12, g)
        assert min(c) > max(s)
        seen += s + c
    assert seen == list(range(401, 1201)) and len(set(seen)) == 800


def all_equal(monkeypatch):
    monkeypatch.setattr(reward_loop, "sample", lambda mu, *a, **k: mu)


def test_all_candidates_equal_stops_at_generation_three_by_patience(tmp_path, monkeypatch):
    all_equal(monkeypatch)
    assert go(tmp_path, ["--round", "1"]) == 0
    r = recs(tmp_path)
    assert len(r) == 4 and r[0]["validation"]["reward"] is not None
    assert [x["accepted"] for x in r[1:]] == [False] * 3 and [x["patience"] for x in r] == [0, 1, 2, 3]
    assert r[3]["stopped"] == "patience" and all(x["params"] == r[0]["params"] for x in r)
    assert json.loads((tmp_path / "rounds/round-1/policy.json").read_text())["chosen_generation"] == 0


def test_the_generation_cap(tmp_path, monkeypatch):
    all_equal(monkeypatch)
    plan = Plan(**{**PLAN.__dict__, "patience": 99, "max_generations": 5})
    assert go(tmp_path, ["--round", "1"], plan=plan) == 0
    r = recs(tmp_path)
    assert len(r) == 6 and r[-1]["stopped"] == "max_generations" and all(x["stopped"] is None for x in r[:-1])


def test_sigma_shrinks_only_after_a_generation_that_accepts_nothing_with_floors(tmp_path, monkeypatch):
    all_equal(monkeypatch)
    cfg = {**CFG, "shrink": 0.7}
    plan = Plan(**{**PLAN.__dict__, "patience": 99, "max_generations": 7, "base": cfg})
    assert go(tmp_path, ["--round", "1"], plan=plan) == 0
    r = recs(tmp_path)
    assert r[0]["sigma_next"] == {"w": 0.5, "theta": 0.5, "p": 1.0}
    w = [x["sigma_next"]["w"] for x in r]
    assert w[1:4] == [0.35, 0.245, 0.1715] and w[5:] == [0.1, 0.1, 0.1]  # floor 0.1
    assert [x["sigma_next"]["p"] for x in r][3:] == [0.343, 0.25, 0.25, 0.25, 0.25]
    assert r[2]["sigma_used"]["w"] == w[1]  # each generation draws with the previous generation's sigma_next


# --- the configuration keys ---------------------------------------------------------------------

def test_each_configuration_key_does_what_the_menu_says(tmp_path, monkeypatch):
    rng = SplitMix64(5)
    big = {"w": 3.0, "theta": 2.0, "p": 3.0}
    mu = Params((1.0, 1.0, 1.0, 1.0), 3.0, 4)
    for cfg, frozen in ((CFG, frozenset()), ({**CFG, "fix_theta": True}, frozenset()),
                        ({**CFG, "frozen_vital": "spo2"}, reward_loop.frozen_set({**CFG, "frozen_vital": "spo2"}, True))):
        draws = [sample(mu, big, rng, cfg, frozen) for _ in range(300)]
        assert (len({d.theta for d in draws}) == 1) == cfg["fix_theta"]
        assert all(d.weights[2] == 0.0 for d in draws) == (cfg["frozen_vital"] == "spo2")
        assert len({d.weights[0] for d in draws}) > 1
    a = sample(mu, big, SplitMix64(9), CFG, frozenset())
    b = sample(mu, big, SplitMix64(9), {**CFG, "fix_theta": True}, frozenset())
    assert (a.weights, a.p) == (b.weights, b.p) and b.theta == 3.0  # same stream, only theta differs
    assert reward_loop.frozen_set(CFG, False) == {"temperature"}
    assert reward_loop.frozen_set({**CFG, "frozen_vital": "spo2"}, False) == {"spo2", "temperature"}
    assert start_params(generation_zero(), frozenset({"spo2"})).weights == (1.0, 1.0, 0.0, 1.0)
    # sigma0: 0.5 (p 1.0), 0.25 (p 0.5), 1.0 (p 2.0)
    assert [sigma_start({**CFG, "sigma0": s}) for s in (0.5, 0.25, 1.0)] == [
        {"w": 0.5, "theta": 0.5, "p": 1.0}, {"w": 0.25, "theta": 0.25, "p": 0.5}, {"w": 1.0, "theta": 1.0, "p": 2.0}]
    # form is passed to every scoring call; selection_trials sets the batch sizes
    seen = []
    real = reward_loop.score_grid
    monkeypatch.setattr(reward_loop, "score_grid", lambda p, t, form, r: seen.append(form) or real(p, t, form, r))
    cfg = {**CFG, "form": "max", "selection_trials": 3, "frozen_vital": "heart_rate", "sigma0": 0.25}
    plan = Plan(**{**PLAN.__dict__, "base": cfg, "max_generations": 2})
    assert go(tmp_path, ["--round", "1"], plan=plan) == 0
    r = recs(tmp_path)
    assert set(seen) == {"max"} and r[0]["params"]["weights"]["heart_rate"] == 0.0
    assert all(len(x["selection_seeds"]) == 3 and len(x["confirmation_seeds"]) == 3 for x in r[1:])
    assert r[1]["sigma_used"] == {"p": 0.5, "theta": 0.25, "w": 0.25}
    assert all(x["params"]["weights"]["heart_rate"] == 0.0 for x in r)


def test_parse_change_allows_exactly_one_menu_change():
    inc = dict(CFG)
    assert parse_change("form=max", inc)[1] == {"key": "form", "from": "sum", "to": "max"}
    assert parse_change("selection_trials=16", inc)[0]["selection_trials"] == 16
    assert parse_change("fix_theta=true", inc)[0]["fix_theta"] is True
    assert parse_change("sigma0=1", inc)[0]["sigma0"] == 1.0
    assert parse_change("frozen_vital=spo2", inc)[1]["to"] == "spo2"
    for bad in ("form=mean", "shrink=0.5", "selection_trials=9", "fix_theta=1", "nokey=1", "form", "frozen_vital=none",
                "form=max,shrink=0.7", "sigma0=true", "form=sum"):
        with pytest.raises(TrialError):
            parse_change(bad, inc)
    assert set(MENU) == set(DEFAULT_CONFIG) and len(MENU) == 6


# --- rounds, files, resume ----------------------------------------------------------------------

def test_a_round_writes_the_schema_and_a_hash_chain(finished):
    r = recs(finished)
    assert len(r) >= 4 and [x["generation"] for x in r] == list(range(len(r)))
    g1 = r[1]
    assert g1["schema"] == 2 and g1["round"] == 1 and g1["generator_seed"] == reward_loop.SEED + 1001
    assert g1["selection_seeds"] == [111, 112] and g1["confirmation_seeds"] == [113, 114, 115] and g1["next_seed"] == 116
    assert g1["config_sha256"] == reward_loop.digest(g1["config"])
    assert r[0]["prev_sha256"] == "none" and r[1]["prev_sha256"] == reward_loop.sha((finished / "rounds/round-1/gen-00.json").read_bytes())
    for x in r:
        assert x["sha256"] == reward_loop.digest({k: v for k, v in x.items() if k != "sha256"})
    assert r[-1]["stopped"] in ("patience", "max_generations") and all(x["stopped"] is None for x in r[:-1])
    pol = json.loads((finished / "rounds/round-1/policy.json").read_text())
    assert (finished / "policy.json").read_bytes() == (finished / "rounds/round-1/policy.json").read_bytes()
    best = max((x for x in r if x["validation"]["reward"] is not None), key=lambda x: (x["validation"]["reward"], -x["generation"]))
    assert pol["chosen_generation"] == best["generation"] and pol["params"] == best["params"]
    assert pol["validation_seeds"] == [101, 102, 103]


def test_the_toy_cohort_has_something_to_learn(finished):
    r = recs(finished)
    assert any(x["accepted"] for x in r[1:])
    acc = next(x for x in r[1:] if x["accepted"])
    c = acc["confirmation"]
    assert c["mean"] > c["threshold"] and not c["winner_rejected"] and [e["seed"] for e in c["d"]] == acc["confirmation_seeds"]


def test_no_seed_is_reused_and_validation_is_never_in_a_batch(tmp_path):
    assert go(tmp_path, ["--round", "1"]) == 0
    assert go(tmp_path, ["--round", "2", "--change", "form=max", "--why", "test"]) == 0
    used, last_sel = [], None
    for rnd in (1, 2):
        for x in recs(tmp_path, rnd)[1:]:
            assert min(x["confirmation_seeds"]) > max(x["selection_seeds"])
            used += x["selection_seeds"] + x["confirmation_seeds"]
    assert used == sorted(used) and len(set(used)) == len(used) and not set(used) & set(PLAN.val_seeds)
    assert min(used) == 111
    led = read_ledger(tmp_path / reward_loop.LEDGER)
    assert led[1]["seeds"]["first"] == led[0]["seeds"]["last"] + 1


def test_missing_seeds_exit_3_with_the_range_and_resume_equals_an_uninterrupted_run(tmp_path, capsys):
    whole = tmp_path / "whole"
    assert go(whole, ["--round", "1"]) == 0
    n_gen = len(recs(whole))
    part = tmp_path / "part"
    assert go(part, ["--round", "1"], MemSource(missing={102})) == 3
    assert capsys.readouterr().err.strip() == "needs seeds 101 to 103" and not part.exists()
    g3 = generation_seeds(111, 2, 3, 3)
    src = MemSource(missing=g3[0] + g3[1])
    assert go(part, ["--round", "1"], src) == 3
    assert capsys.readouterr().err.strip() == f"needs seeds {g3[0][0]} to {g3[1][-1]}"
    assert sorted(p.name for p in (part / "rounds/round-1").glob("gen-*")) == ["gen-00.json", "gen-01.json", "gen-02.json"]
    assert not (part / "rounds/round-1/policy.json").exists()
    src.missing.clear()
    assert go(part, ["--round", "1"], src) == 0
    assert tree(part) == tree(whole) and n_gen >= 4
    assert go(part, ["--round", "1"]) == 0 and tree(part) == tree(whole)  # idempotent: the ledger line is not doubled


def test_same_inputs_same_bytes(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assert go(a, ["--round", "1"]) == 0 and go(b, ["--round", "1"]) == 0
    assert tree(a) == tree(b)


def test_a_finished_run_rewrites_its_policy_and_deletes_a_stray_tmp(tmp_path):
    assert go(tmp_path, ["--round", "1"]) == 0
    want = tree(tmp_path)
    (tmp_path / "rounds/round-1/policy.json").unlink()
    (tmp_path / "rounds/round-1/gen-07.json.tmp").write_text("half")
    assert go(tmp_path, ["--round", "1"]) == 0 and tree(tmp_path) == want


@pytest.fixture
def copy(finished, tmp_path):
    shutil.copytree(finished, tmp_path / "o")
    return tmp_path / "o"


def test_a_tampered_record_is_refused(copy, capsys):
    p = copy / "rounds/round-1/gen-01.json"
    p.write_text(p.read_text().replace('"next_seed": 116', '"next_seed": 117'))
    assert go(copy, ["--round", "1"]) == 2 and "sha256" in capsys.readouterr().err


def test_a_broken_link_a_gap_a_changed_config_and_a_file_after_stop_are_refused(copy, capsys):
    base = tree(copy)
    # an edited record with its own hash made good: the next file's link breaks
    reseal(copy / "rounds/round-1/gen-01.json", lambda r: r.update(reason="edited"))
    assert go(copy, ["--round", "1"]) == 2 and "prev_sha256" in capsys.readouterr().err
    (copy / "rounds/round-1/gen-01.json").write_bytes(base["rounds/round-1/gen-01.json"])
    (copy / "rounds/round-1/gen-01.json").unlink()  # a gap
    assert go(copy, ["--round", "1"]) == 2 and "gap" in capsys.readouterr().err
    (copy / "rounds/round-1/gen-01.json").write_bytes(base["rounds/round-1/gen-01.json"])
    last = sorted((copy / "rounds/round-1").glob("gen-*.json"))[-1]
    extra = copy / "rounds/round-1" / f"gen-{int(last.name[4:6]) + 1:02d}.json"
    extra.write_bytes(last.read_bytes())  # a file after the stopped record
    assert go(copy, ["--round", "1"]) == 2 and "stopped" in capsys.readouterr().err
    extra.unlink()
    plan = Plan(**{**PLAN.__dict__, "base": {**CFG, "shrink": 0.7}})
    assert go(copy, ["--round", "1"], plan=plan) == 2 and "config" in capsys.readouterr().err
    assert go(copy, ["--round", "1"]) == 0  # the untouched files still resume


def test_a_wrong_temperature_flag_and_a_moved_start_are_refused(copy, capsys):
    shutil.copytree(copy / "rounds/round-1", copy / "nt_tmp")
    shutil.rmtree(copy / "no_temperature")
    shutil.copytree(copy / "nt_tmp", copy / "no_temperature/generations")
    (copy / "no_temperature/generations/policy.json").unlink()
    assert go(copy, ["--no-temperature"]) == 2 and "temperature flag" in capsys.readouterr().err
    shutil.rmtree(copy / "rounds/round-1")
    shutil.copytree(copy / "nt_tmp", copy / "rounds/round-1")
    reseal(copy / "rounds/round-1/gen-00.json", lambda r: r["params"].update(theta=2.5))
    assert go(copy, ["--round", "1"]) == 2 and "starting params" in capsys.readouterr().err


def test_jobs_do_not_change_the_bytes_and_folders_read_like_memory(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    for s in [*PLAN.val_seeds, *range(111, 111 + 20)]:
        _write_folder(root, s)
    a, b = tmp_path / "a", tmp_path / "b"
    assert go(a, ["--data", str(root), "--round", "1", "--jobs", "1"], src=reward_loop.FolderSource(root, PLAN)) == 0
    assert go(b, ["--data", str(root), "--round", "1", "--jobs", "2"], src=reward_loop.FolderSource(root, PLAN)) == 0
    assert tree(a) == tree(b) and len(recs(a)) >= 4


def test_folders_missing_a_summary_exit_3_and_write_nothing(tmp_path, capsys):
    root = tmp_path / "data"
    root.mkdir()
    for s in PLAN.val_seeds:
        _write_folder(root, s)
    assert go(tmp_path / "o", ["--data", str(root), "--round", "1"], src=reward_loop.FolderSource(root, PLAN)) == 3
    assert capsys.readouterr().err.strip() == "needs seeds 111 to 115" and not (tmp_path / "o").exists()


# --- the split ----------------------------------------------------------------------------------

def test_the_loop_never_opens_held_out_or_unowned_seeds(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    for s in [*PLAN.val_seeds, *range(111, 111 + 20)]:
        _write_folder(root, s)  # no seed-11..30, no seed-61..340 folders at all
    opened = []
    real = trials._read
    monkeypatch.setattr(trials, "_read", lambda folder, name, cap: opened.append((folder.name, name)) or real(folder, name, cap))
    assert go(tmp_path / "o", ["--data", str(root), "--round", "1"], src=reward_loop.FolderSource(root, PLAN)) == 0
    allowed = {f"seed-{s}" for s in [*PLAN.val_seeds, *range(111, 211)]}
    assert opened and {f for f, _ in opened} <= allowed


def test_held_out_and_ext_seeds_are_refused_before_any_file_is_opened(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(trials, "_read", lambda *a: opened.append(a))
    for seed in (11, 20, 30, 61, 100, 340, 5, 2801):
        with pytest.raises(TrialError, match="not allowed"):
            load_seed(tmp_path, seed, Plan().allowed)
        with pytest.raises(TrialError, match="not allowed"):
            reward_loop.FolderSource(tmp_path, Plan()).check([seed])
    assert not opened


def test_manifest_naming_another_seed_and_a_different_shape_are_refused_before_the_cohort(tmp_path):
    for s in (101, 102):
        _write_folder(tmp_path, s)
        (tmp_path / f"seed-{s}" / "cohort.csv").unlink()
    m = tmp_path / "seed-101/manifest.json"
    m.write_text(m.read_text().replace('"seed": 101', '"seed": 15'))
    with pytest.raises(TrialError, match="manifest says seed 15"):
        load_seed(tmp_path, 101, PLAN.allowed)
    ref = reward_loop.manifest_shape(tmp_path, 102, PLAN.allowed)
    m2 = tmp_path / "seed-102/manifest.json"
    m2.write_text(m2.read_text().replace('"speed": 60', '"speed": 10'))
    with pytest.raises(TrialError, match="differ from the validation"):
        load_seed(tmp_path, 102, PLAN.allowed, ref)  # refused although cohort.csv is gone too
    m2.write_text(m2.read_text().replace('"speed": 10', '"speed": 60').replace("0" * 64, "1" * 64))
    with pytest.raises(TrialError, match="differ from the validation"):
        load_seed(tmp_path, 102, PLAN.allowed, ref)


# --- the outer loop -----------------------------------------------------------------------------

def fake_round(monkeypatch, v):
    """Round 2 ships a policy whose per-seed validation rewards are the incumbent's plus v."""
    def fake(spec, source, plan, runner, log=print):
        inc = json.loads((spec.gen_dir.parent / "round-1" / "policy.json").read_text())
        per = [{"seed": x["seed"], "reward": x["reward"] + d} for x, d in zip(inc["validation_per_seed"], v)]
        body = {**{k: x for k, x in inc.items() if k != "sha256"}, "round": 2, "config": spec.cfg,
                "config_sha256": reward_loop.digest(spec.cfg), "validation_per_seed": per, "chosen_generation": 1}
        pol = {**body, "sha256": reward_loop.digest(body)}
        raw = reward_loop._dump(pol)
        reward_loop._write(spec.policy_path, raw)
        return pol, reward_loop.sha(raw), [{"next_seed": spec.start_seed + 10, "stopped": "patience"}]
    monkeypatch.setattr(reward_loop, "run_inner", fake)


@pytest.mark.parametrize("v, decision", [([0.5, 0.3, 0.7], "kept"),  # mean 0.5 > 2 x 0.2 / sqrt(3) = 0.2309
                                          ([0.2, 0.0, 0.4], "dropped"),  # mean 0.2 not above 0.2309
                                          ([0.1, 0.1, 0.1], "kept"),  # stdev 0: any positive mean
                                          ([0.0, 0.0, 0.0], "dropped"), ([-0.5, -0.3, -0.7], "dropped")])
def test_kept_and_dropped_on_hand_made_numbers(tmp_path, monkeypatch, v, decision):
    assert go(tmp_path, ["--round", "1"]) == 0
    fake_round(monkeypatch, v)
    assert go(tmp_path, ["--round", "2", "--change", "form=max", "--why", "a test"]) == 0
    led = read_ledger(tmp_path / reward_loop.LEDGER)
    line = led[1]
    assert line["decision"] == decision and line["change"] == {"key": "form", "from": "sum", "to": "max"}
    assert line["vs_incumbent"]["margin"] == pytest.approx(2 * line["vs_incumbent"]["stdev"] / 3 ** 0.5, abs=1e-6)
    assert line["started_from"]["round"] == 1 and line["why"] == "a test"
    top = (tmp_path / "policy.json").read_bytes()
    assert top == (tmp_path / f"rounds/round-{2 if decision == 'kept' else 1}/policy.json").read_bytes()


def test_a_round_shipping_nothing_is_dropped(tmp_path, monkeypatch):
    assert go(tmp_path, ["--round", "1"]) == 0
    monkeypatch.setattr(reward_loop, "run_inner", lambda spec, *a, **k: (None, "", [{"next_seed": 130, "stopped": "patience"}]))
    assert go(tmp_path, ["--round", "2", "--change", "form=max", "--why", "x"]) == 0
    assert read_ledger(tmp_path / reward_loop.LEDGER)[1]["shipped"] is None
    assert read_ledger(tmp_path / reward_loop.LEDGER)[1]["decision"] == "dropped"


def test_a_round_starts_from_the_incumbent_with_one_change(tmp_path, monkeypatch):
    assert go(tmp_path, ["--round", "1"]) == 0
    inc = json.loads((tmp_path / "rounds/round-1/policy.json").read_text())
    assert go(tmp_path, ["--round", "2", "--change", "frozen_vital=spo2", "--why", "w"]) == 0
    g0 = recs(tmp_path, 2)[0]
    assert g0["config"] == {**CFG, "frozen_vital": "spo2"} and g0["params"]["weights"]["spo2"] == 0.0
    assert g0["prev_sha256"] == reward_loop.sha((tmp_path / "rounds/round-1/policy.json").read_bytes())
    assert g0["params"]["theta"] == inc["params"]["theta"] and g0["next_seed"] == inc["seeds"]["last"] + 1
    assert g0["sigma_next"] == sigma_start(g0["config"])  # sigma reset


@pytest.mark.parametrize("argv, text", [(["--round", "2"], "exactly one"),
                                         (["--round", "2", "--change", "form=max"], "exactly one"),
                                         (["--round", "2", "--change", "form=sum", "--why", "x"], "already"),
                                         (["--round", "2", "--change", "form=max,fix_theta=true", "--why", "x"], "takes one of"),
                                         (["--round", "3", "--change", "form=max", "--why", "x"], "in order"),
                                         (["--round", "1", "--change", "form=max", "--why", "x"], "baseline"),
                                         (["--round", "5"], "in order"),
                                         (["--round", "2", "--change", "form=max", "--why", "bad\nline"], "printable"),
                                         (["--round", "2", "--change", "form=max", "--why", "x" * 601], "600")])
def test_a_round_needs_exactly_one_change(tmp_path, capsys, argv, text):
    assert go(tmp_path, ["--round", "1"]) == 0
    capsys.readouterr()
    assert go(tmp_path, argv) == 2 and text in capsys.readouterr().err


def test_the_ledger_is_chained_and_ends_with_stop_then_no_temperature(finished):
    led = read_ledger(finished / reward_loop.LEDGER)
    assert [x["decision"] for x in led] == ["baseline", "stop", "no_temperature"]
    lines = (finished / reward_loop.LEDGER).read_text().splitlines()
    assert led[0]["prev_line_sha256"] == "none" and led[1]["prev_line_sha256"] == reward_loop.sha(lines[0])
    assert led[2]["prev_line_sha256"] == reward_loop.sha(lines[1])
    assert list(json.loads(lines[0])) == sorted(json.loads(lines[0])) and led[1]["reason"] == "one round is enough here"
    pol = json.loads((finished / "rounds/round-1/policy.json").read_text())
    assert led[0]["shipped"]["generation"] == pol["chosen_generation"] and led[0]["change"] is None
    assert led[0]["shipped"]["policy_sha256"] == reward_loop.sha((finished / "rounds/round-1/policy.json").read_bytes())
    assert led[0]["seeds"] == pol["seeds"] and led[0]["started_from"] is None and led[0]["vs_incumbent"] is None
    nt = json.loads((finished / "no_temperature/policy.json").read_text())
    assert nt["params"]["weights"]["temperature"] == 0.0 and nt["temperature"] is False and led[2]["seeds"]["first"] == 111
    assert all(x["params"]["weights"]["temperature"] == 0.0 for x in recs_nt(finished))


def recs_nt(out):
    return [json.loads(p.read_text()) for p in sorted((out / "no_temperature/generations").glob("gen-*.json"))]


def test_a_tampered_ledger_is_refused(copy, capsys):
    p = copy / reward_loop.LEDGER
    lines = p.read_text().splitlines()
    p.write_text("\n".join([lines[0].replace("baseline", "kept"), *lines[1:]]) + "\n")
    assert go(copy, ["--stop", "x"]) == 2 and "out of order" in capsys.readouterr().err
    p.write_text("\n".join([lines[0], lines[2]]) + "\n")  # a line removed from the chain
    assert go(copy, ["--stop", "x"]) == 2 and "link" in capsys.readouterr().err
    p.write_text("\n".join(lines) + "\n")
    assert go(copy, ["--stop", "one round is enough here"]) == 0  # the same stop again is idempotent
    assert go(copy, ["--stop", "a different reason"]) == 2


def test_stop_and_no_temperature_order(tmp_path, capsys):
    assert go(tmp_path, ["--stop", "x"]) == 2 and "no round" in capsys.readouterr().err
    assert go(tmp_path, ["--round", "1"]) == 0
    assert go(tmp_path, ["--no-temperature"]) == 2 and "stop line" in capsys.readouterr().err
    assert go(tmp_path, ["--stop", "four rounds are done"]) == 0
    assert go(tmp_path, ["--round", "2", "--change", "form=max", "--why", "x"]) == 2
    assert "stop line" in capsys.readouterr().err
    assert go(tmp_path, ["--stop", "bad\x07"]) == 2


def test_the_budget_rule(tmp_path, monkeypatch, capsys):
    all_equal(monkeypatch)  # round 1 uses 3 generations x 5 seeds: 111 to 125
    plan = lambda last: Plan(**{**PLAN.__dict__, "last_seed": last})  # noqa: E731
    assert go(tmp_path / "a", ["--round", "1"], plan=plan(145)) == 0
    # round 2 starts at 126 and needs 4 x 5 = 20 seeds: 126 to 145 fits, 144 does not
    assert go(tmp_path / "a", ["--round", "2", "--change", "form=max", "--why", "x"], plan=plan(145)) == 0
    assert go(tmp_path / "b", ["--round", "1"], plan=plan(144)) == 0
    assert go(tmp_path / "b", ["--round", "2", "--change", "form=max", "--why", "x"], plan=plan(144)) == 2
    assert "budget" in capsys.readouterr().err
    assert go(tmp_path / "b", ["--stop", "the budget is spent"]) == 0
    assert go(tmp_path / "c", ["--round", "1"], plan=plan(100)) == 2  # not even round 1 fits


# --- held-out, once -----------------------------------------------------------------------------

def test_evaluate_refuses_a_tampered_or_missing_policy(copy, tmp_path, capsys):
    top = copy / "policy.json"
    obj = json.loads(top.read_text())
    obj["params"]["theta"] += 1
    top.write_text(json.dumps(obj))
    assert go(copy, ["--evaluate", str(tmp_path)]) == 2 and "sha256" in capsys.readouterr().err
    top.write_bytes((copy / "rounds/round-1/policy.json").read_bytes())
    (copy / "no_temperature/policy.json").unlink()
    assert go(copy, ["--evaluate", str(tmp_path)]) == 2 and "run the loop first" in capsys.readouterr().err
    shutil.copy(copy / "rounds/round-1/policy.json", copy / "no_temperature/policy.json")  # the wrong temperature flag
    assert go(copy, ["--evaluate", str(tmp_path)]) == 2 and "temperature flag" in capsys.readouterr().err


def test_evaluate_scores_held_out_rows_from_the_policy_files_config(finished, tmp_path, capsys, monkeypatch):
    root = tmp_path / "ward-v2"
    root.mkdir()
    for s in (11, 12):
        _write_folder(root, s)
    monkeypatch.setattr(evaluate, "_sanity", lambda *a: None)  # tiny cohort: the sanity guard is for real data
    seen = []
    real = reward_loop.policies
    monkeypatch.setattr(reward_loop, "policies", lambda n, p, form="sum": seen.append((n, form)) or real(n, p, form))
    assert go(finished, ["--evaluate", str(root)]) == 0
    out = capsys.readouterr().out
    assert [n for n, _ in seen] == ["reward_gen0", "reward_final", "reward_gen0_no_temp", "reward_final_no_temp"]
    for name in ("news2_chart_trigger", "reward_final", "news2_or_reward_final_no_temp", "baseline"):
        assert name in out
    assert out.rstrip().endswith("synthetic, not validated: evaluated on the same simulator that generated the data")


def test_evaluate_json_holds_the_printed_numbers_and_leaves_the_text_alone(finished, tmp_path, capsys, monkeypatch):
    from helpers import check_rules_against_text
    root = tmp_path / "ward-v2"
    root.mkdir()
    for s in (11, 12):
        _write_folder(root, s)
    monkeypatch.setattr(evaluate, "_sanity", lambda *a: None)
    assert go(finished, ["--evaluate", str(root)]) == 0
    plain = capsys.readouterr().out
    path = tmp_path / "held.json"
    assert go(finished, ["--evaluate", str(root), "--json", str(path)]) == 0
    assert capsys.readouterr().out == plain
    obj = json.loads(path.read_text())
    ids = [r["id"] for r in obj["rules"]]
    assert "reward_final" in ids and "news2_or_reward_final_no_temp" in ids and obj["context"]["trials"] == 2
    check_rules_against_text(obj["rules"], plain)
    assert go(finished, ["--evaluate", str(root), "--json", str(tmp_path / "no" / "h.json")]) == 2
    assert "--json" in capsys.readouterr().err
    assert go(finished, ["--stop", "x", "--json", str(path)]) == 2


def test_usage_errors(tmp_path, capsys):
    assert main(["--out", str(tmp_path)]) == 2
    assert main(["--out", str(tmp_path), "--round", "1", "--stop", "x"]) == 2
    assert main(["--out", str(tmp_path), "--round", "1"]) == 2 and "--data" in capsys.readouterr().err
    assert main(["--out", str(tmp_path), "--bogus"]) == 2
    assert main(["--out", str(tmp_path), "--round", "1", "--jobs", "0"], source=MemSource()) == 2


# --- round 2 additions: validation rejection, t table, schedule, folders, evaluate --------------

def _vtrial(seed):
    return SimpleNamespace(seed=seed, episodes=3, news2_on=5, cohort_rows=100)


def test_val_marks_a_policy_rejected_on_any_validation_seed(monkeypatch):
    val = [_vtrial(s) for s in (1, 2, 3)]
    mk_ = lambda rej: [Score(1.0, 0, 0.0, 0.0, r) for r in rej]  # noqa: E731
    monkeypatch.setattr(reward_loop, "score_grid", lambda *a: [mk_([False, True, False])])
    v = reward_loop._val(generation_zero(), val, "sum", Runner())
    assert v["reward"] is None and v["rejected_time_in_alert"] and len(v["per_seed"]) == 3
    monkeypatch.setattr(reward_loop, "score_grid", lambda *a: [mk_([False, False, False])])
    v = reward_loop._val(generation_zero(), val, "sum", Runner())
    assert v["reward"] == 1.0 and not v["rejected_time_in_alert"]


def reject_on_validation(monkeypatch, only_changed: bool):
    """Time in alert breaks on validation seed 102 (all validation seeds when only_changed is False),
    for every policy except generation 0's."""
    real = reward_loop.score
    g0 = generation_zero()

    def fake(params, t, form="sum"):
        s = real(params, t, form)
        bad = t.seed in PLAN.val_seeds and (not only_changed or (params != g0 and t.seed == 102))
        return Score(s.reward, s.caught_early, s.alerts_per_12h, s.time_in_alert, s.rejected or bad)

    monkeypatch.setattr(reward_loop, "score", fake)


def test_a_rejected_validation_does_not_improve_and_is_never_shipped(tmp_path, monkeypatch):
    reject_on_validation(monkeypatch, only_changed=True)
    assert go(tmp_path, ["--round", "1"]) == 0
    r = recs(tmp_path)
    assert r[0]["validation"]["reward"] is not None
    acc = [x for x in r[1:] if x["accepted"]]
    assert acc, "the toy must accept once so that a changed policy meets the rejection"
    for x in r[1:]:
        if x["params"] != r[0]["params"]:
            assert x["validation"]["reward"] is None and x["validation"]["rejected_time_in_alert"]
    first = r[r.index(acc[0])]
    assert first["best_generation"] == 0 and first["patience"] == 1  # rejected: not improved, patience up
    assert all(x["best_generation"] == 0 for x in r)  # a rejected generation is never the best
    assert json.loads((tmp_path / "rounds/round-1/policy.json").read_text())["chosen_generation"] == 0


def test_a_round_where_every_generation_is_rejected_ships_nothing(tmp_path, monkeypatch):
    reject_on_validation(monkeypatch, only_changed=False)
    assert go(tmp_path, ["--round", "1"]) == 0
    r = recs(tmp_path)
    assert all(x["validation"]["reward"] is None for x in r) and r[-1]["best_generation"] is None
    assert [x["patience"] for x in r] == [0, 1, 2, 3] and r[-1]["stopped"] == "patience"
    assert not (tmp_path / "rounds/round-1/policy.json").exists() and not (tmp_path / "policy.json").exists()
    line = read_ledger(tmp_path / reward_loop.LEDGER)[0]
    assert line["decision"] == "dropped" and line["shipped"] is None


def test_t_values_for_other_confirmation_sizes():
    assert (T_CRIT[3], T_CRIT[5], T_CRIT[12]) == (2.920, 2.132, 1.796)
    ok, mean, sd, thr = confirm_test([1.0, 2.0, 3.0])  # mean 2, sd 1, 2.920 / sqrt(3)
    assert (mean, sd, round(thr, 4), ok) == (2.0, 1.0, 1.6859, True)
    assert not confirm_test([0.5, 1.5, 2.5])[0]  # mean 1.5 is under 1.6859 (1.796 would accept: 1.0369)
    base = [-2.0, -1.0, 0.0, 1.0, 2.0]  # sd 1.5811, threshold 2.132 x 1.5811 / sqrt(5) = 1.5076
    assert confirm_test([x + 1.52 for x in base])[0] and not confirm_test([x + 1.49 for x in base])[0]
    assert round(confirm_test([x + 1.0 for x in base])[3], 4) == 1.5076


def test_a_confirmation_size_without_a_t_value_is_refused_cleanly(tmp_path, capsys):
    for n in (13, 1, 40):
        plan = Plan(**{**PLAN.__dict__, "confirm": n})
        assert go(tmp_path, ["--round", "1"], plan=plan) == 2
        err = capsys.readouterr().err
        assert err.startswith("refused:") and len(err.strip().splitlines()) == 1 and "confirmation size" in err
        assert go(tmp_path, ["--no-temperature"], plan=plan) == 2
        capsys.readouterr()
    assert not tmp_path.exists() or not list(tmp_path.iterdir())


def test_seeds_off_the_schedule_are_refused_even_with_a_good_hash(copy, capsys):
    reseal(copy / "rounds/round-1/gen-01.json", lambda r: r.update(selection_seeds=[111, 113]))
    assert go(copy, ["--round", "1"]) == 2
    err = capsys.readouterr().err
    assert "not the schedule" in err and "sha256" not in err and "prev_sha256" not in err


def test_seed_folders_with_a_manifest_but_no_summary_exit_3(tmp_path, capsys):
    root = tmp_path / "data"
    root.mkdir()
    for s in [*PLAN.val_seeds, *range(111, 116)]:
        _write_folder(root, s)
    (root / "seed-113" / "summary.json").unlink()
    src = lambda: reward_loop.FolderSource(root, PLAN)  # noqa: E731
    assert go(tmp_path / "o", ["--data", str(root), "--round", "1"], src=src()) == 3
    assert capsys.readouterr().err.strip() == "needs seeds 111 to 115" and not (tmp_path / "o").exists()
    (root / "seed-102" / "summary.json").unlink()  # validation first
    assert go(tmp_path / "o", ["--data", str(root), "--round", "1"], src=src()) == 3
    assert capsys.readouterr().err.strip() == "needs seeds 101 to 103" and not (tmp_path / "o").exists()


def test_evaluate_refuses_a_wrong_folder_before_reading_a_cohort(finished, tmp_path, monkeypatch, capsys):
    ext = tmp_path / "ext"
    ext.mkdir()
    _write_folder(ext, 100)
    opened = []
    real = trials._read
    monkeypatch.setattr(trials, "_read", lambda f, n, c: opened.append((f.name, n)) or real(f, n, c))
    assert go(finished, ["--evaluate", str(ext)]) == 2 and "no held-out trials" in capsys.readouterr().err
    assert not opened
    bad = tmp_path / "bad"
    bad.mkdir()
    _write_folder(bad, 11)
    (bad / "seed-11" / "manifest.json").write_text((bad / "seed-11" / "manifest.json").read_text().replace('"seed": 11', '"seed": 100'))
    (bad / "seed-11" / "cohort.csv").unlink()
    assert go(finished, ["--evaluate", str(bad)]) == 2 and "another seed" in capsys.readouterr().err
    assert {n for _, n in opened} == {"manifest.json"}


def test_evaluate_needs_the_stop_line_and_the_ledgers_incumbent(copy, tmp_path, capsys):
    root = tmp_path / "w"
    root.mkdir()
    _write_folder(root, 11)
    led = (copy / reward_loop.LEDGER).read_text().splitlines()
    (copy / reward_loop.LEDGER).write_text(led[0] + "\n")  # no stop line yet
    assert go(copy, ["--evaluate", str(root)]) == 2 and "no stop line" in capsys.readouterr().err
    (copy / reward_loop.LEDGER).write_text("\n".join(led) + "\n")
    # a different, internally valid policy file at the top: not the ledger's incumbent
    reseal(copy / "policy.json", lambda r: r["params"].update(theta=r["params"]["theta"] + 0.5))
    assert go(copy, ["--evaluate", str(root)]) == 2 and "incumbent" in capsys.readouterr().err


def test_held_out_generation_zero_rows_are_always_the_default_start(tmp_path, monkeypatch, capsys):
    cfg = {**CFG, "form": "max", "frozen_vital": "heart_rate"}
    plan = Plan(**{**PLAN.__dict__, "base": cfg})
    assert go(tmp_path, ["--round", "1"], plan=plan) == 0
    assert go(tmp_path, ["--stop", "done"], plan=plan) == 0
    assert go(tmp_path, ["--no-temperature"], plan=plan) == 0
    root = tmp_path / "w"
    root.mkdir()
    _write_folder(root, 11)
    monkeypatch.setattr(evaluate, "_sanity", lambda *a: None)
    seen = {}
    real = reward_loop.policies
    monkeypatch.setattr(reward_loop, "policies", lambda n, p, form="sum": seen.update({n: (p, form)}) or real(n, p, form))
    assert go(tmp_path, ["--evaluate", str(root)], plan=plan) == 0
    assert seen["reward_gen0"] == (Params((1.0, 1.0, 1.0, 1.0), 3.0, 4), "sum")
    assert seen["reward_gen0_no_temp"] == (Params((1.0, 1.0, 1.0, 0.0), 3.0, 4), "sum")
    assert seen["reward_final"][1] == "max" and seen["reward_final_no_temp"][1] == "max"


@pytest.mark.parametrize("field, value", [("theta", 10**400), ("theta", "3.0"), ("theta", True), ("p", 2.0),
                                          ("heart_rate", "1"), ("heart_rate", False)])
def test_a_policy_file_with_a_bad_number_is_refused(copy, tmp_path, capsys, field, value):
    root = tmp_path / "w"
    root.mkdir()
    _write_folder(root, 11)

    def edit(r):
        (r["params"]["weights"] if field == "heart_rate" else r["params"])[field] = value

    reseal(copy / "policy.json", edit)
    assert go(copy, ["--evaluate", str(root)]) == 2 and "bad params" in capsys.readouterr().err


def test_an_out_that_cannot_be_written_is_refused(tmp_path, capsys):
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    assert go(blocker, ["--round", "1"]) == 2
    assert capsys.readouterr().err.startswith("refused:")


def test_a_generation_with_a_no_episode_trial_runs_and_records_d_zero(tmp_path, monkeypatch):
    from test_reward import no_episode_trial
    monkeypatch.setitem(_PREP, 113, prepare(no_episode_trial(113)))  # a confirmation seed of generation 1
    monkeypatch.setitem(_PREP, 102, prepare(no_episode_trial(102)))  # and a validation seed
    a, b = tmp_path / "a", tmp_path / "b"
    assert go(a, ["--round", "1"]) == 0 and go(b, ["--round", "1"]) == 0 and tree(a) == tree(b)
    r = recs(a)
    c = r[1]["confirmation"]
    assert c is not None and not c["winner_rejected"] and not c["current_rejected"]
    assert dict((e["seed"], e["d"]) for e in c["d"])[113] == 0.0
    ps = {x["seed"]: x for x in r[0]["validation"]["per_seed"]}
    assert ps[102]["episodes"] == 0 and ps[102]["reward"] == 0.0 and ps[102]["caught_early"] == 0
    part = tmp_path / "part"
    src = MemSource(missing={113})
    assert go(part, ["--round", "1"], src) == 3
    src.missing.clear()
    assert go(part, ["--round", "1"], src) == 0 and tree(part) == tree(a)  # resume, same bytes


def test_read_policy_reads_current_and_legacy_files_and_refuses_a_tampered_one_of_either(finished, tmp_path):
    from research import reward_loop_b6
    cur = finished / "rounds/round-1/policy.json"
    assert reward_loop.read_policy(cur, True) == Params.from_dict(json.loads(cur.read_text())["params"])
    val = [prepare(toy(s)) for s in reward_loop_b6.VAL_SEEDS]
    recs = list(reward_loop_b6.learn(lambda g: [prepare(toy(s)) for s in reward_loop_b6.generation_seeds(g)], val,
                                     True, generations=1, candidates=2))
    old = reward_loop_b6.policy_file(recs, True)
    assert "config" not in old
    for obj, path in ((json.loads(cur.read_text()), tmp_path / "cur.json"), (old, tmp_path / "old.json")):
        path.write_text(json.dumps(obj))
        assert isinstance(reward_loop.read_policy(path, True), Params)
        obj["params"]["theta"] += 1
        path.write_text(json.dumps(obj))
        with pytest.raises(TrialError, match="does not match its sha256"):
            reward_loop.read_policy(path, True)
