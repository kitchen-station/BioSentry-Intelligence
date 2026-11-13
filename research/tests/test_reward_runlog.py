"""Slice M6: the reward loop's run log. It only reads committed records: no trial is opened, no clock."""

import json
import re
import shutil

import pytest

from research import reward_loop, trials
from research.reward_loop import read_ledger
from test_reward_loop import finished, go  # noqa: F401  (finished is a fixture)


def make(out, tmp_path, name="log.json", *extra):
    path = tmp_path / name
    code = go(out, ["--run-log", str(path), "--run-id", "m2-test.1", "--commit", "abc1234", *extra])
    return code, path


def test_the_run_log_is_deterministic_and_complete(finished, tmp_path, monkeypatch):  # noqa: F811
    def no_trial(*a, **k):
        raise AssertionError("the run log opened a trial")
    monkeypatch.setattr(trials, "load_trial", no_trial)
    monkeypatch.setattr(reward_loop, "load_trial", no_trial)
    (c1, p1), (c2, p2) = make(finished, tmp_path, "a.json"), make(finished, tmp_path, "b.json")
    assert (c1, c2) == (0, 0) and p1.read_bytes() == p2.read_bytes()
    log = json.loads(p1.read_text())
    led = read_ledger(finished / "experiments.jsonl")
    assert (log["model"], log["run_id"], log["commit"]) == ("tier_b/reward_policy", "m2-test.1", "abc1234")
    assert log["framework"].startswith("none") and log["time"]["recorded"] is False
    assert log["stop"] == led[1] and [r["ledger"] for r in log["runs"]] == [led[0], led[2]]
    assert [r["name"] for r in log["runs"]] == ["round 1", "no temperature"]
    assert [r["temperature"] for r in log["runs"]] == [True, False]
    for run, folder in zip(log["runs"], (finished / "rounds/round-1", finished / "no_temperature/generations")):
        files = [json.loads(f.read_text()) for f in sorted(folder.glob("gen-*.json"))]
        assert [g["generation"] for g in run["generations"]] == list(range(len(files)))
        assert run["generations"][-1]["stopped"] == run["ledger"]["stop_reason"]
        for g, f in zip(run["generations"], files):
            assert g["validation_reward"] == f["validation"]["reward"] and g["sigma_next"] == f["sigma_next"]
            assert g["accepted"] == f["accepted"] and g["params"] == f["params"] and g["patience"] == f["patience"]
            if g["generation"]:
                assert g["selection"]["winner_mean"] == (f["selection"]["winner"] or {}).get("mean")
                assert g["selection"]["current_mean"] == f["selection"]["current_mean"]
                assert g["sigma_used"] == f["sigma_used"] and g["selection_seeds"]["first"] == f["selection_seeds"][0]
                if f["confirmation"]:
                    c = f["confirmation"]
                    assert g["confirmation"] == {"n": len(c["d"]), "mean_d": c["mean"], "stdev_d": c["stdev"],
                                                 "threshold": c["threshold"], "winner_rejected": c["winner_rejected"]}
    text = p1.read_text()
    assert str(tmp_path) not in text and not re.search(r'"/(app|tmp|Users|home|private|data)', text)


def test_the_run_log_refuses_an_unfinished_ledger_a_bad_id_and_a_tampered_record(finished, tmp_path, capsys):  # noqa: F811
    copy = tmp_path / "copy"
    shutil.copytree(finished, copy)
    lines = (copy / "experiments.jsonl").read_text().splitlines(True)
    (copy / "experiments.jsonl").write_text("".join(lines[:1]))  # no stop line yet
    assert make(copy, tmp_path)[0] == 2 and "no stop line" in capsys.readouterr().err
    for bad in (["--run-id", "a b"], ["--commit", "two words"]):
        assert go(finished, ["--run-log", str(tmp_path / "y.json"), *bad]) == 2
        assert capsys.readouterr().err.count("\n") == 1
    assert not (tmp_path / "y.json").exists()
    shutil.copytree(finished, tmp_path / "t")
    gen = tmp_path / "t/rounds/round-1/gen-01.json"
    gen.write_text(gen.read_text().replace('"patience"', '"patience": 9, "x"', 1))  # a changed byte, no new hash
    assert make(tmp_path / "t", tmp_path, "z.json")[0] == 2 and "sha256" in capsys.readouterr().err
    assert go(finished, ["--run-id", "x", "--round", "1"]) == 2  # the flags go with --run-log only
