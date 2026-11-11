import json
from dataclasses import replace

import pytest

from helpers import copy_toy, trial
from research import reward_loop
from research.reward_loop import (NO_TEMP_DIR, TRAIN_SEEDS, VAL_SEEDS, generation_seeds, learn, load_seed,
                                  policy_file, read_policy, sample)
from research.bootstrap import SplitMix64
from research.reward_policy import Params, Prepared, generation_zero, mean_reward, prepare, score, states, z_series
from research.rescore import score_row
from research.trials import COHORT_HEADER, EPISODES_HEADER, Episode, Row, TrialError


def mk(patient: str, minute: int, hr, rr=16.0, spo2=97.0, temp=36.8) -> Row:
    r = Row(patient, minute, hr, rr, spo2, temp, 120, None, "A", False, 1, False, None)
    total = getattr(score_row(r), "total", None)
    return replace(r, ranked=total is not None, news2=total)


def stay(patient: str, i: int, seed: int, episode: bool) -> tuple[list[Row], Episode]:
    """Arithmetic only: 24+ quiet bins, then (episode) a small 2 h heart-rate ramp that generation 0
    mostly misses (z past 3 only late in the ramp), so the loop has something to learn."""
    start = 15 * (40 + (i * 7 + seed) % 30) + 0.5 if episode else None
    rows = []
    for b in range(1, 95):
        m = 15 * b
        w = 0.0 if not episode or not start <= m < start + 150 else min(1.0, (m - start) / 120)  # recovers
        wob = ((b * 7 + i + seed) % 5) - 2
        rows.append(mk(patient, m, 70 + i % 4 + wob + 9 * w, 16 + wob % 2, 97, 36.8))
    return rows, Episode(patient, 15, None, "respiratory" if episode else None, start, 120.0 if episode else None)


def toy(seed: int, patients: int = 6):
    rows, eps = [], []
    for i in range(patients):
        r, e = stay(f"p-{i:03d}", i, seed, i % 2 == 0)
        rows += r
        eps.append(e)
    # one steadily unwell patient: NEWS2 5 the whole stay (rr 25, SpO2 92), flat against own baseline
    rows += [mk("p-sick", 15 * b, 80, 25, 92) for b in range(1, 95)]
    eps.append(Episode("p-sick", 15, None, None, None, None))
    return trial(rows, eps, seed=seed, patient_days=(patients + 1) * 94 * 15 / 1440)


def series(hrs: list) -> dict[int, Row]:
    return {15 * (i + 1): mk("p", 15 * (i + 1), h) for i, h in enumerate(hrs) if h != "gap"}


def on(st: dict) -> list[int]:
    return [m for m, s in st.items() if s]


HR_ONLY = Params((1.0, 0.0, 0.0, 0.0), 2.0, 2)


def test_causal_a_bin_never_reads_later_bins():
    full = series([70, 72] * 12 + [95, 95, 70, 95, 95, 95])
    zs = z_series(full)
    for t in sorted(full):
        prefix = {m: r for m, r in full.items() if m <= t}
        assert z_series(prefix) == zs[: len(prefix)]
        assert states(HR_ONLY, z_series(prefix)) == {m: s for m, s in states(HR_ONLY, zs).items() if m <= t}


def test_missing_value_contributes_nothing_and_not_ranked_is_not_scored():
    rows = series([70, 72] * 12 + [95, 95, 95])
    rows[25 * 15] = mk("p", 25 * 15, None)  # hr missing: SpO2 etc. present, NEWS2 abstains
    zs = dict(z_series(rows))
    assert not rows[25 * 15].ranked and zs[25 * 15] is None  # NOT RANKED: no score
    assert on(states(HR_ONLY, list(zs.items()))) == [27 * 15]  # the run restarts after it
    assert all(z == (0.0, 0.0, 0.0, 0.0) for m, z in zs.items() if m <= 23 * 15)  # baseline forming
    # a RANKED row with a missing vital (not seen on ward-v1): that vital gives 0, never a fill
    forced = dict(rows)
    forced[25 * 15] = replace(rows[25 * 15], ranked=True, news2=0)
    assert dict(z_series(forced))[25 * 15][0] == 0.0
    # a gap (no row) also ends the run, nothing is filled in
    gap = series([70, 72] * 12 + [95, "gap", 95, 95])
    assert on(states(HR_ONLY, z_series(gap))) == [28 * 15]


def test_reward_arithmetic_and_time_in_alert_rejection():
    rows = {m: mk("a", m, 70) for m in range(15, 300, 15)}
    z = (0.0, 0.0, 0.0, 0.0)
    zs = [(m, (2.0, 0.0, 0.0, 0.0) if m in (150, 165) else z) for m in sorted(rows)]
    t = Prepared(31, {"a": rows}, {"a": zs}, {"a": (100.0, 200.0)}, episodes=1, cohort_rows=19,
                 shifts=1.0, news2_rate=2.0, news2_on=2)
    s = score(Params((1.0, 0.0, 0.0, 0.0), 1.0, 1), t)
    # caught early (alerting at 150 and 165, inside 100..200), 1 alert per 12 h: 1 - 0.5 * (1 / 2) * 1
    assert (s.caught_early, s.alerts_per_12h, s.reward, s.rejected) == (1, 1.0, 0.75, False)
    assert s.time_in_alert == 2 / 19
    # alerting only after fully in: not caught early; reward 0 - 0.25
    late = replace(t, early={"a": (100.0, 140.0)})
    assert score(Params((1.0, 0.0, 0.0, 0.0), 1.0, 1), late).reward == -0.25
    # more alerting bins than NEWS2's: rejected, and the mean over the set is None
    tight = replace(t, news2_on=1)
    assert score(Params((1.0, 0.0, 0.0, 0.0), 1.0, 1), tight).rejected
    assert mean_reward(Params((1.0, 0.0, 0.0, 0.0), 1.0, 1), [t, tight])[0] is None
    assert mean_reward(Params((1.0, 0.0, 0.0, 0.0), 1.0, 1), [t, late])[0] == 0.25


def test_params_are_validated():
    for bad in [((-1.0, 0, 0, 0), 1.0, 1), ((1.0, 0, 0, 0), 0.0, 1), ((1.0, 0, 0, 0), 1.0, 9),
                ((1.0, 0, 0, 0), 1.0, 1.0), ((float("nan"), 0, 0, 0), 1.0, 1)]:
        with pytest.raises(ValueError):
            Params(*bad)
    assert generation_zero() == Params((1.0, 1.0, 1.0, 1.0), 3.0, 4)


def test_seeds_never_reused_and_never_held_out():
    seeds = [s for g in range(1, 11) for s in generation_seeds(g)]
    assert seeds == list(range(31, 61)) and len(set(seeds)) == 30
    assert not set(TRAIN_SEEDS) & set(range(1, 31)) and set(VAL_SEEDS) == {8, 9, 10}


def test_held_out_refused_in_training(tmp_path):
    for seed, allowed in ((15, range(1, 61)), (11, TRAIN_SEEDS), (30, VAL_SEEDS), (7, VAL_SEEDS)):
        with pytest.raises(TrialError, match="not allowed"):
            load_seed(tmp_path, seed, allowed)
    # a folder named seed-08 whose manifest says 15: refused before cohort.csv is opened
    folder = copy_toy(tmp_path / "seed-08", **{"manifest.json": ('"seed": 11', '"seed": 15')})
    (folder / "cohort.csv").unlink()
    with pytest.raises(TrialError, match="manifest says seed 15"):
        load_seed(tmp_path, 8, VAL_SEEDS)


def _run(temperature: bool) -> list[dict]:
    val = [prepare(toy(s)) for s in VAL_SEEDS]
    return list(learn(lambda g: [prepare(toy(s)) for s in generation_seeds(g)], val, temperature,
                      generations=3, candidates=6))


def test_a_generation_never_accepts_a_worse_policy():
    recs = _run(True)
    assert [r["train_seeds"] for r in recs[1:]] == [generation_seeds(g) for g in (1, 2, 3)]
    for prev, r in zip(recs, recs[1:]):
        assert r["current_before"]["params"] == prev["params"]
        before, best = r["current_before"]["train_reward"], r["best_candidate"]
        if r["accepted"]:
            assert best is not None and (before is None or best["train_reward"] > before)
            assert r["params"] == best["params"]
        else:
            assert r["params"] == prev["params"]
            assert best is None or before is not None and best["train_reward"] <= before
    assert any(r.get("accepted") for r in recs)  # the toy has something to learn


@pytest.mark.parametrize("temperature", [True, False])
def test_deterministic_bytes(temperature):
    a, b = _run(temperature), _run(temperature)
    dump = lambda rs: [reward_loop._dump(r) for r in rs] + [reward_loop._dump(policy_file(rs, temperature))]  # noqa: E731
    assert dump(a) == dump(b)


def test_no_temperature_weight_is_exactly_zero():
    recs = _run(False)
    params = [r["params"] for r in recs] + [r["best_candidate"]["params"] for r in recs[1:] if r["best_candidate"]]
    assert all(p["weights"]["temperature"] == 0.0 for p in params)
    rng = SplitMix64(1)
    assert all(sample(generation_zero(False), {"w": 5.0, "theta": 1.0, "p": 2.0}, rng, False).weights[3] == 0.0
               for _ in range(200))


def test_policy_file_hash_is_checked(tmp_path):
    pol = policy_file(_run(True), True)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(pol))
    assert read_policy(path, True) == Params.from_dict(pol["params"])
    with pytest.raises(TrialError, match="temperature flag"):
        read_policy(path, False)
    pol["params"]["theta"] += 1
    path.write_text(json.dumps(pol))
    with pytest.raises(TrialError, match="does not match its sha256"):
        read_policy(path, True)


@pytest.mark.parametrize("field, value", [("theta", 10**400), ("theta", "3.0"), ("theta", True),
                                          ("heart_rate", "1"), ("heart_rate", False), ("p", 2.0)])
def test_policy_file_with_a_bad_number_is_refused(tmp_path, field, value):
    pol = policy_file(_run(True), True)
    body = {k: v for k, v in pol.items() if k != "sha256"}
    if field == "heart_rate":
        body["params"]["weights"][field] = value
    else:
        body["params"][field] = value
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({**body, "sha256": reward_loop.digest(body)}))
    with pytest.raises(TrialError, match="bad params"):
        read_policy(path, True)


def test_two_evaluated_episodes_for_one_patient_are_refused():
    t = toy(31)
    with pytest.raises(TrialError, match="two evaluated episodes"):
        prepare(replace(t, episodes=t.episodes + (t.episodes[0],)))


def _write_folder(root, seed: int) -> None:
    """A toy trial as a format-version-1 folder (values rounded, NEWS2 recomputed so parity holds)."""
    t = toy(seed)
    d = root / f"seed-{seed:02d}"
    d.mkdir()
    num = lambda x: "" if x is None else repr(round(float(x), 4))  # noqa: E731
    lines = [COHORT_HEADER]
    for r in t.rows:
        r = mk(r.patient, r.minute, *(round(getattr(r, v), 4) for v in ("heart_rate", "respiratory_rate", "spo2", "temperature")))
        lines.append(",".join([r.patient, str(r.minute), num(r.heart_rate), num(r.respiratory_rate), num(r.spo2),
                               num(r.temperature), "120", "70", "", "A", "0", "1", "100", str(int(r.ranked)),
                               "" if r.news2 is None else str(r.news2)]))
    (d / "cohort.csv").write_text("\n".join(lines) + "\n")
    eps = [EPISODES_HEADER]
    for e in t.episodes:
        eps.append(",".join([e.patient, str(e.admit_minute), "", "", e.kind or "", num(e.start), num(e.ramp_minutes), "", "", "0"]))
    (d / "episodes.csv").write_text("\n".join(eps) + "\n")
    (d / "manifest.json").write_text(json.dumps({
        "format_version": 1, "synthetic": True, "seed": seed, "days": t.days, "speed": 60,
        "scenario_sha256": "0" * 64, "sim_commit": "abcdef1", "node_major": 22, "bin_minutes": 15,
        "patients": len(t.episodes), "episodes": sum(e.kind is not None for e in t.episodes)}))
    (d / "summary.json").write_text(json.dumps({"patient_days": t.patient_days}))


@pytest.fixture(scope="module")
def folders(tmp_path_factory):
    root = tmp_path_factory.mktemp("trials")
    (root / "train").mkdir()
    (root / "val").mkdir()
    for s in TRAIN_SEEDS:
        _write_folder(root / "train", s)
    for s in VAL_SEEDS:
        _write_folder(root / "val", s)
    return root


def test_run_loop_uses_fresh_seeds_and_ships_the_best_on_validation(folders, tmp_path, capsys):
    out = tmp_path / "out"
    assert reward_loop.main(["--train", str(folders / "train"), "--validate", str(folders / "val"),
                             "--out", str(out)]) == 0
    recs = [json.loads((out / "generations" / f"gen-{g:02d}.json").read_text()) for g in range(11)]
    assert [r["train_seeds"] for r in recs[1:]] == [list(range(31 + 3 * i, 34 + 3 * i)) for i in range(10)]
    ok = [r for r in recs if r["validation"]["reward"] is not None]
    best = max(ok, key=lambda r: (r["validation"]["reward"], -r["generation"]))
    pol = json.loads((out / "policy.json").read_text())
    assert pol["chosen_generation"] == best["generation"] != 10  # not simply the last generation
    assert read_policy(out / "policy.json", True) == Params.from_dict(best["params"])


def test_an_out_that_cannot_be_written_is_refused(folders, tmp_path, capsys):
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    assert reward_loop.main(["--train", str(folders / "train"), "--validate", str(folders / "val"),
                             "--out", str(blocker)]) == 2
    assert capsys.readouterr().err.startswith("refused:")
