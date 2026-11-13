"""The evaluation export: kinds, caps, banned words, and the two files. Toy trials only: no held-out seed is read here."""

import json
import re
from pathlib import Path

import pytest

from helpers import TOY, check_rules_against_text
from research import evaluate, export_evaluation as ex
from research.export_evaluation import Refused

ROOT = Path(__file__).parent.parent.parent
COMMIT = "a" * 40
ARGS = ["--date", "2026-11-13", "--commit", COMMIT, "--source", "python -m research.export_evaluation x"]


@pytest.fixture(autouse=True)
def _no_params_file(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate, "PARAMS_PATH", tmp_path / "none.json")  # the DEFAULTS rows


def make(tmp_path, command, *extra, name="out.json"):
    out = tmp_path / name
    assert ex.main([command, *ARGS, *extra, "--out", str(out)]) == 0
    return out.read_bytes(), json.loads(out.read_bytes())


def toy_held_out(tmp_path):
    """The toy table as text and as JSON from one run."""
    result: dict = {}
    text = evaluate.run([str(TOY)], result=result)
    path = tmp_path / "toy-held.json"
    evaluate.write_json(str(path), result)
    return text, result, path


# --- kinds, rounding, caps, banned words --------------------------------------------------------------

def test_rounding_by_kind_and_no_exponent_or_negative_zero():
    assert ex.D1(47.249) == 47.2 and ex.D3(0.36263) == 0.363 and ex.D1(0.04) == 0.0
    assert repr(ex.D3(0.00004)) == "0.0" and repr(ex.D3(-0.0004)) == "0.0"
    assert ex.INT(91.0) == 91 and ex.D3(None) is None and ex.INT(None) is None
    for bad in (float("nan"), float("inf"), True, "1"):
        with pytest.raises(Refused):
            ex.D3(bad)
    with pytest.raises(Refused, match="whole number"):
        ex.INT(1.5)
    with pytest.raises(Refused, match="plain spelling"):
        ex.check_tree({"x": [1e-05]})
    ex.check_tree({"x": [0.0001, 12345678.5], "sha": "e6b6ae44" + "1e5" * 5})


@pytest.mark.parametrize("text", ["AI predicts deterioration", "a reinforcement learning loop", "Self-learning",
                                  "a Conformal guarantee", "fewer false alarms than NEWS2", "it is validated"])
def test_banned_words_are_refused_but_not_validated_passes(text):
    with pytest.raises(Refused):
        ex.check_tree({"a": [text]})
    ex.check_tree({"a": ["Synthetic, not validated.", "unvalidated", "validation reward", "Not validated"]})


def test_caps():
    with pytest.raises(Refused):
        ex.cap([0] * 31, 30, "rules")
    with pytest.raises(Refused):
        ex.short("x" * 301, 300, "note")


def test_a_banned_word_stops_the_command_and_writes_nothing(tmp_path, capsys):
    text, result, path = toy_held_out(tmp_path)
    result["context"]["tuning"] = "A reinforcement learning step."
    path.write_text(json.dumps(result))
    out = tmp_path / "e.json"
    assert ex.main(["evaluations", *ARGS, "--held-out", str(path), "--out", str(out)]) == 2
    assert not out.exists() and not Path(str(out) + ".tmp").exists()
    assert capsys.readouterr().err.count("\n") == 1


def test_argument_errors_are_one_line_refusals(tmp_path, capsys):
    for argv in (["registry"], ["registry", "--date", "2026-1-1", "--commit", COMMIT, "--out", str(tmp_path / "x")],
                 ["registry", "--date", "2026-11-13", "--commit", "abc", "--out", str(tmp_path / "x")],
                 ["models", *ARGS, "--out", str(tmp_path / "x")], ["held-out-from-text", "a", "--out", "b"],
                 ["evaluations", *ARGS, "--out", str(tmp_path / "x")]):
        assert ex.main(argv) == 2
    assert capsys.readouterr().err.count("\n") == 6 and not (tmp_path / "x").exists()


# --- --json on research.evaluate ------------------------------------------------------------------------

def test_json_flag_leaves_the_text_byte_identical_and_holds_the_printed_numbers(tmp_path, capsys):
    assert evaluate.main([str(TOY)]) == 0
    plain = capsys.readouterr().out
    path = tmp_path / "h.json"
    assert evaluate.main([str(TOY), "--json", str(path)]) == 0
    assert capsys.readouterr().out == plain
    assert plain == (Path(__file__).parent / "data" / "toy_expected.txt").read_text()
    obj = json.loads(path.read_text())
    assert obj["format"] == "biosentry-held-out/1" and path.read_text().endswith("}\n")
    check_rules_against_text(obj["rules"], plain)
    chart = obj["rules"][0]
    assert chart["id"] == "news2_chart_trigger" and (chart["new_alert"]["n"], chart["new_alert"]["d"]) == (1, 2)
    assert chart["lead"]["n"] == 1 and chart["lead"]["median"] == 90.0 and chart["lead"]["q1"] is None
    assert chart["time_in_alert"]["rate"] != round(chart["time_in_alert"]["rate"], 3)  # full precision, not rounded
    assert "/" not in json.dumps(obj["context"])
    again = tmp_path / "h2.json"
    assert evaluate.main([str(TOY), "--json", str(again)]) == 0 and again.read_bytes() == path.read_bytes()


def test_the_toy_text_is_unchanged_by_the_json_pass():
    expected = (Path(__file__).parent / "data" / "toy_expected.txt").read_text()
    assert evaluate.run([str(TOY)], result={}) == expected


def test_an_unwritable_json_path_refuses_before_any_trial_is_read(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(evaluate, "load_trial", lambda f: pytest.fail("a trial was read"))
    assert evaluate.main([str(TOY), "--json", str(tmp_path / "no" / "such" / "h.json")]) == 2
    assert evaluate.main([str(TOY), "--json"]) == 2
    assert capsys.readouterr().err.count("\n") == 2 and list(tmp_path.iterdir()) == []


# --- the two files ---------------------------------------------------------------------------------------

def test_registry_file(tmp_path):
    raw, obj = make(tmp_path, "registry")
    body = obj["body"]
    assert raw == (json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode()
    assert obj["format"] == "biosentry-evaluation/2" and obj["section"] == "registry" and list(body) == ["datasets", "open_weights"]
    prov = obj["provenance"]
    assert (prov["synthetic"], prov["validated"], prov["commit"], prov["date"]) == (True, False, COMMIT, "2026-11-13")
    assert prov["exporter"] == {"name": "research.export_evaluation", "version": "1"}
    ds = {d["id"]: d for d in body["datasets"]}
    assert set(ds) == {"eicu-crd-demo", "ward-v2", "ward-v2-ext"}
    assert ds["eicu-crd-demo"]["licence"] == "Open Data Commons Open Database License v1.0 (ODbL)"
    assert ds["eicu-crd-demo"]["name"] == "eICU-CRD Demo 2.0.1" and ds["ward-v2"]["licence"] == "not stated"
    assert all(set(e) == {"id", "name", "path", "licence", "source"} and e["path"] is None for e in body["datasets"] + body["open_weights"])
    assert body["open_weights"] == [{"id": "Qwen/Qwen2.5-0.5B-Instruct", "name": "Qwen2.5-0.5B-Instruct", "path": None,
                                     "licence": "Apache-2.0", "source": "models/agent/card.md"}]
    assert make(tmp_path, "registry", name="b.json")[0] == raw
    assert ex.main(["registry", "--date", "2026-11-14", "--commit", COMMIT, "--out", str(tmp_path / "c.json")]) == 0
    assert (tmp_path / "c.json").read_bytes() != raw


def test_evaluations_file_from_a_toy_held_out_json(tmp_path):
    text, result, path = toy_held_out(tmp_path)
    raw, obj = make(tmp_path, "evaluations", "--held-out", str(path))
    body = obj["body"]
    assert obj["format"] == "biosentry-evaluation/4" and obj["section"] == "evaluations"
    assert list(body) == ["confidence", "context", "rules"] and body["confidence"] is None
    assert any("confidence is null" in n for n in obj["notes"])
    assert body["context"]["patient_days"] == ex.D1(result["context"]["patient_days"]) and body["context"]["trials"] == 1
    assert [r["id"] for r in body["rules"]] == [r["id"] for r in result["rules"]]
    r0 = body["rules"][0]
    assert r0["name"] == "NEWS2 chart trigger" and r0["adds_to"] is None and body["rules"][3]["adds_to"] == "news2"
    assert r0["new_alert"] == {"n": 1, "d": 2, "rate": 0.5, "lo": ex.D3(result["rules"][0]["new_alert"]["lo"]),
                               "hi": ex.D3(result["rules"][0]["new_alert"]["hi"])}
    assert obj["provenance"]["seeds"] == [{"role": "held out", "seeds": [11]}]
    assert any("reward policy's rules are absent" in n for n in obj["notes"])
    assert make(tmp_path, "evaluations", "--held-out", str(path), name="b.json")[0] == raw
    # the same rule in two files is kept once; a different one refuses; a drop removes one rule
    twice = make(tmp_path, "evaluations", "--held-out", str(path), "--held-out", str(path), name="twice.json")[1]
    assert twice["body"]["rules"] == body["rules"] and any("more than one" in n for n in twice["notes"])
    dropped = make(tmp_path, "evaluations", "--held-out", str(path), "--drop-rule", "news2_total_ge_5", name="d.json")[1]
    assert len(dropped["body"]["rules"]) == len(body["rules"]) - 1
    other = json.loads(path.read_text())
    other["rules"][0]["any_bin"]["n"] += 1
    other_path = tmp_path / "other-held.json"
    other_path.write_text(json.dumps(other))
    bad = tmp_path / "bad.json"
    assert ex.main(["evaluations", *ARGS, "--held-out", str(path), "--held-out", str(other_path), "--out", str(bad)]) == 2
    other = json.loads(path.read_text())
    other["context"]["patients"] += 1
    other_path.write_text(json.dumps(other))
    assert ex.main(["evaluations", *ARGS, "--held-out", str(path), "--held-out", str(other_path), "--out", str(bad)]) == 2
    assert ex.main(["evaluations", *ARGS, "--held-out", str(path), "--drop-rule", "nope", "--out", str(bad)]) == 2
    assert not bad.exists()


def test_a_reward_rule_removes_the_absence_note_and_more_than_thirty_rules_are_refused():
    r = {"id": "x", "per_shift": {"value": 0.1, "lo": 0.1, "hi": 0.1}, "lead": {"n": 0, "median": None, "q1": None, "q3": None},
         **{k: {"n": 1, "d": 2, "rate": 0.5} for k in ("time_in_alert", "any_bin_not_cut_short", "chance_new", "chance_any")},
         **{k: {"n": 1, "d": 2, "rate": 0.5, "lo": None, "hi": None} for k in ("new_alert", "any_bin")},
         "excess_any_not_cut_short": None}
    res: dict = {}
    evaluate.run([str(TOY)], result=res)
    f = {"format": "biosentry-held-out/1", "context": res["context"], "rules": [{**r, "id": f"r{i}"} for i in range(31)]}
    with pytest.raises(Refused, match="31 rules"):
        ex.merge_held_out([f], [])
    kept = ex.merge_held_out([f], ["r0"])
    assert len(kept[1]) == 30 and any("dropped" in n and "r0" in n for n in kept[3])
    g = {**f, "rules": [{**r, "id": "reward_final"}]}
    assert not any("absent" in n for n in ex.merge_held_out([g], [])[3])


def test_the_confidence_block_is_a_rounded_copy_of_the_committed_file(tmp_path):
    import hashlib
    src = ROOT / "models" / "tier_c" / "confidence_check" / "heldout.json"
    _, _, path = toy_held_out(tmp_path)
    _, obj = make(tmp_path, "evaluations", "--held-out", str(path), "--confidence", str(src))
    c, real = obj["body"]["confidence"], json.loads(src.read_bytes())["confidence"]
    assert c["source"] == {"path": "models/tier_c/confidence_check/heldout.json", "sha256": hashlib.sha256(src.read_bytes()).hexdigest()}
    assert set(c["variants"]) == set(real) == {"all", "no_temp"}

    def walk(a, b):
        assert type(a) is type(b) and (not isinstance(a, dict) or a.keys() == b.keys())
        if isinstance(a, dict):
            for k in a:
                walk(a[k], b[k])
        elif isinstance(a, float):
            assert abs(a - b) <= 5e-5 and len(repr(a).split(".")[1]) <= 4 and "e" not in repr(a)
        else:
            assert a == b  # counts, text (the verdict as written) and booleans unchanged
    walk(c["variants"], real)
    assert c["variants"]["all"]["verdict"] in ("MET", "NOT MET")
    outside = tmp_path / "elsewhere.json"
    outside.write_bytes(src.read_bytes())
    assert ex.main(["evaluations", *ARGS, "--held-out", str(path), "--confidence", str(outside), "--out", str(tmp_path / "x")]) == 2
