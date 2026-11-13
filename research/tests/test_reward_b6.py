"""The legacy B6 loop (research/reward_loop_b6.py), kept because research/agent/ drives it."""
import json

import pytest

from helpers import copy_toy
from research import reward_loop_b6 as reward_loop
from research.reward_loop_b6 import (TRAIN_SEEDS, VAL_SEEDS, generation_seeds, learn, load_seed,
                                     policy_file, read_policy, sample)
from research.bootstrap import SplitMix64
from research.reward_policy import Params, generation_zero, prepare
from research.trials import TrialError
from test_reward import _write_folder, toy


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
