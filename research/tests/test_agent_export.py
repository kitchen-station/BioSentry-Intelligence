import json
from pathlib import Path

import pytest

from research.agent import __main__ as cli
from research.agent import ask, ledger
from research.agent import export as exporting
from test_agent_ask import cfg  # noqa: F401  (ten toy trials as tuning seeds)
from test_agent_ledger import tree_hash
from test_agent_planner import env_for
from test_agent_tools import make_run

COMMIT = "c8f0ce82f1a48155cd3eba717c7af3ed9244dcab"
REAL = Path(__file__).resolve().parents[1] / "agent" / "records" / "pr19-run" / "ledger.jsonl"
Q1 = "What if we alerted at a NEWS2 total of 5 or more?"


def export(cfg, tmp_path, *extra, name="out.json", date="2026-11-13", commit=True):  # noqa: F811
    argv = ["export", "--date", date, "--out", str(tmp_path / name), *(["--commit", COMMIT] if commit else []), *extra]
    return cli.main(argv, env_for(cfg))


def load(path):
    def no_float(x):
        raise AssertionError(f"float {x} in the file")
    return json.loads(Path(path).read_text(), parse_float=no_float)


def good_scores(**changes):
    run = {"label": "before", "model": "m", "answered": 3, "refused": 1, "grounding_passed": 2, "correct_template": 2}
    s = {"question_set": "set", "n": 4, "runs": [run, {**run, "label": "after", "model": "m + LoRA"}]}
    return {"scores": {**s, **changes}, "notes": ["one note"]}


def write(tmp_path, obj, name="scores.json"):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return str(p)


def test_export_has_the_contract_shape_and_changes_nothing(cfg, tmp_path, capsys):  # noqa: F811
    ask.ask(cfg, Q1)
    ask.ask(cfg, "What is the capital of France?")
    sid = ledger.begin_step(cfg, "round", "goal", {"planner": "rule", "rule": "R5", "experiment": "sweep_baseline",
                                                   "tool": "sweep_baseline", "args": {}})
    ledger.add_step(cfg, sid, [{"tool": "sweep_baseline", "args": {}, "ok": True, "result": {}}], "done")
    make_run(cfg, sid)
    pid = ledger.propose(cfg, "adopt_reward_policy", sid, f"runs/{sid}", False, {})
    ledger.begin_step(cfg, "ask", "never finished", {"rule": "none"})
    before_ledger, before_models = (cfg.home / "ledger.jsonl").read_bytes(), tree_hash(cfg.models)
    assert export(cfg, tmp_path, "--source", "cmd one", "--source", "cmd\x1b two") == 0
    assert capsys.readouterr().out.strip() == "exported 2 questions, 1 proposals"
    d = load(tmp_path / "out.json")
    assert (d["format"], d["section"], d["notes"]) == (
        "biosentry-evaluation/1", "research-agent", ["1 questions are left out: the tool failed or the step did not finish."])
    prov = d["provenance"]
    assert prov["commit"] == COMMIT and prov["date"] == "2026-11-13" and prov["source"] == ["cmd one", "cmd? two"]
    assert prov["synthetic"] is True and prov["validated"] is False and prov["data"] == "ward-v2 simulator trials"
    assert prov["seeds"] == [{"role": "tuning", "seeds": list(range(1, 11))}]
    assert prov["exporter"] == {"name": "research.agent export", "version": "1"}
    assert prov["repo"] == "kitchen-station/BioSentry-Intelligence"
    b = d["body"]
    assert b["ledger"]["records"] == len(ledger.read(cfg)) and b["scores"] is None
    q1, q2 = b["questions"]
    assert (q1["template"], q1["status"], q1["question"]) == ("news2_threshold", "answered", Q1)
    assert len(q1["answer"]) == 4 and q1["answer"][-1] == ask.LAST_LINE and "20" in q1["numbers"]
    assert (q2["template"], q2["status"], q2["answer"], q2["numbers"]) == ("none", "refused", ask.REFUSAL.splitlines(), [])
    assert q1["asked_at"] <= q2["asked_at"]
    assert b["proposals"] == [{"id": pid, "action": "adopt_reward_policy", "step": sid, "conclusion": "done",
                               "state": "proposed", "approved_by": None, "approved_at": None}]
    assert (cfg.home / "ledger.jsonl").read_bytes() == before_ledger and tree_hash(cfg.models) == before_models
    ledger.approve(cfg, pid, "Ada Lovelace")
    ledger.apply(cfg, pid)
    assert export(cfg, tmp_path) == 0
    p = load(tmp_path / "out.json")["body"]["proposals"][0]
    assert (p["state"], p["approved_by"]) == ("applied", "Ada Lovelace") and p["approved_at"]


def test_the_file_is_sorted_indented_one_line_end_and_the_same_every_time(cfg, tmp_path):  # noqa: F811
    ask.ask(cfg, Q1)
    assert export(cfg, tmp_path, name="a.json") == 0 and export(cfg, tmp_path, name="b.json") == 0
    raw = (tmp_path / "a.json").read_bytes()
    assert raw == (tmp_path / "b.json").read_bytes() and raw.endswith(b"}\n") and not raw.endswith(b"\n\n")
    d = load(tmp_path / "a.json")
    assert raw.decode() == json.dumps(d, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    assert not (tmp_path / "a.json.tmp").exists()


def test_the_committed_ledger_exports_three_answers_one_refusal_one_applied_proposal(cfg, tmp_path, capsys):  # noqa: F811
    argv = ["export", "--date", "2026-11-13", "--commit", COMMIT, "--ledger", str(REAL), "--out", str(tmp_path / "r.json")]
    assert cli.main(argv, env_for(cfg)) == 0
    assert capsys.readouterr().out.strip() == "exported 4 questions, 1 proposals"
    b = load(tmp_path / "r.json")["body"]
    assert b["ledger"]["records"] == 21 and len(b["questions"]) == 4
    answered = [q for q in b["questions"] if q["status"] == "answered"]
    assert len(answered) == 3 and sum(q["status"] == "refused" for q in b["questions"]) == 1
    assert all(len(q["answer"]) >= 3 and q["numbers"] for q in answered)
    assert [p["state"] for p in b["proposals"]] == ["applied"]


def test_a_stored_conclusion_that_was_altered_is_refused(cfg, tmp_path, capsys):  # noqa: F811
    ask.ask(cfg, Q1)
    start, step = ledger.read(cfg)
    sid = ledger.begin_step(cfg, "ask", Q1, start["plan"])
    ledger.add_step(cfg, sid, step["calls"], step["conclusion"].replace("20 of 20", "19 of 20"), grounding="ok")
    assert export(cfg, tmp_path) == 2
    assert f"{sid}: the stored answer does not match" in capsys.readouterr().err and not (tmp_path / "out.json").exists()


def test_a_failed_tool_leaves_the_question_out_and_a_failed_grounding_is_a_refusal(cfg, tmp_path):  # noqa: F811
    ask.ask(cfg, Q1)
    start, step = ledger.read(cfg)
    sid = ledger.begin_step(cfg, "ask", "tool fails", start["plan"])
    ledger.add_step(cfg, sid, [{"tool": "evaluate_rules", "args": {}, "ok": False, "error": "x"}], "The tool failed: x")
    sid = ledger.begin_step(cfg, "ask", "grounding fails", start["plan"])
    ledger.add_step(cfg, sid, step["calls"], "Refused: a number in the answer is not in the tool result.", grounding="failed")
    assert export(cfg, tmp_path) == 0
    d = load(tmp_path / "out.json")
    q = d["body"]["questions"]
    assert [x["status"] for x in q] == ["answered", "refused"] and q[1]["template"] == "news2_threshold"
    assert q[1]["answer"] == ask.REFUSAL.splitlines() and d["notes"][0].startswith("1 questions are left out")


def test_export_refuses_the_models_folder_a_broken_chain_and_a_missing_ledger(cfg, tmp_path, capsys):  # noqa: F811
    assert export(cfg, tmp_path, "--ledger", str(tmp_path / "none.jsonl")) == 2
    assert "no such file" in capsys.readouterr().err
    assert cli.main(["export", "--date", "2026-11-13", "--commit", COMMIT, "--out", str(cfg.models / "a.json")], env_for(cfg)) == 2
    assert "does not write inside the models folder" in capsys.readouterr().err
    assert cli.main(["export"], env_for(cfg)) == 2
    ask.ask(cfg, "What is the capital of France?")
    path = cfg.home / "ledger.jsonl"
    path.write_bytes(path.read_bytes().replace(b"capital", b"capitol", 1))
    assert export(cfg, tmp_path, name="x.json") == 2
    assert "broken" in capsys.readouterr().err and not (tmp_path / "x.json").exists()


def test_a_question_with_markup_is_exported_as_plain_text(cfg, tmp_path):  # noqa: F811
    ask.ask(cfg, '<script>alert(1)</script> "quoted" \x1b[31m threshold of 5?')
    assert export(cfg, tmp_path) == 0
    q, = load(tmp_path / "out.json")["body"]["questions"]
    assert q["question"].startswith("<script>alert(1)</script>") and "\x1b" not in q["question"]


def test_an_empty_ledger_exports_empty_lists(cfg, tmp_path, capsys):  # noqa: F811
    assert export(cfg, tmp_path) == 0 and capsys.readouterr().out.strip() == "exported 0 questions, 0 proposals"
    b = load(tmp_path / "out.json")["body"]
    assert (b["questions"], b["proposals"], b["ledger"]["records"]) == ([], [], 0)


def test_scores_are_accepted_and_their_notes_join_the_envelope(cfg, tmp_path):  # noqa: F811
    assert export(cfg, tmp_path, "--scores", write(tmp_path, good_scores())) == 0
    d = load(tmp_path / "out.json")
    assert d["body"]["scores"] == good_scores()["scores"] and d["notes"] == ["one note"]


@pytest.mark.parametrize("bad", [
    {"scores": good_scores()["scores"]},
    {**good_scores(), "extra": 1},
    good_scores(n=3),  # answered + refused is 4
    good_scores(n=-1),
    good_scores(n=4.0),
    good_scores(n=True),
    good_scores(question_set="x" * 301),
    good_scores(extra=1),
    good_scores(runs=good_scores()["scores"]["runs"][:1]),
    good_scores(runs=good_scores()["scores"]["runs"][::-1]),
    {**good_scores(), "notes": ["x" * 301]},
    {**good_scores(), "notes": ["n"] * 11},
    {**good_scores(), "notes": "text"},
    [],
])
def test_a_malformed_scores_file_is_refused(cfg, tmp_path, capsys, bad):  # noqa: F811
    assert export(cfg, tmp_path, "--scores", write(tmp_path, bad)) == 2
    assert capsys.readouterr().err.startswith("refused: ") and not (tmp_path / "out.json").exists()


def test_scores_with_a_wrong_run_field_are_refused(cfg, tmp_path):  # noqa: F811
    for key, value in (("grounding_passed", 4), ("correct_template", 4), ("answered", 1.5), ("model", 3), ("extra", 1)):
        s = good_scores()
        s["scores"]["runs"][1][key] = value
        assert export(cfg, tmp_path, "--scores", write(tmp_path, s)) == 2, key
    assert export(cfg, tmp_path, "--scores", str(tmp_path / "missing.json")) == 2
    (tmp_path / "bad.json").write_text("{")
    assert export(cfg, tmp_path, "--scores", str(tmp_path / "bad.json")) == 2


def test_a_banned_phrase_is_refused_with_the_phrase_and_the_record(cfg, tmp_path, capsys):  # noqa: F811
    ask.ask(cfg, "Is this Self-Learning? What is the capital of France?")
    assert export(cfg, tmp_path) == 2
    err = capsys.readouterr().err
    assert "self-learning" in err and "S-0001" in err
    (cfg.home / "ledger.jsonl").unlink()
    assert export(cfg, tmp_path, "--source", "a Reinforcement Learning run") == 2
    assert "reinforcement learning" in capsys.readouterr().err and not (tmp_path / "out.json").exists()


@pytest.mark.parametrize("extra", [
    ["--commit", "ABC"], ["--commit", COMMIT.upper()], ["--source", "x"] * 11, ["--bogus", "1"], ["--ledger"],
])
def test_bad_arguments_are_refused_in_one_line(cfg, tmp_path, capsys, extra):  # noqa: F811
    argv = ["export", "--date", "2026-11-13", "--out", str(tmp_path / "o.json"), *extra]
    assert cli.main(argv, env_for(cfg)) == 2
    err = capsys.readouterr().err
    assert err.startswith("refused: ") and len(err.splitlines()) == 1 and not (tmp_path / "o.json").exists()


@pytest.mark.parametrize("date", ["2026-13-01", "2026-02-30", "26-11-13", "2026-1-3", "today", "２０２６-11-13"])
def test_a_bad_date_is_refused(cfg, tmp_path, capsys, date):  # noqa: F811
    assert export(cfg, tmp_path, date=date) == 2 and "--date" in capsys.readouterr().err


def test_ten_sources_are_accepted(cfg, tmp_path):  # noqa: F811
    assert export(cfg, tmp_path, *["--source", "x"] * 10) == 0
    assert len(load(tmp_path / "out.json")["provenance"]["source"]) == 10


def test_a_result_over_256_kb_is_refused(cfg, tmp_path, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setattr(exporting, "MAX_FILE", 100)
    assert export(cfg, tmp_path) == 2 and "256 KB" in capsys.readouterr().err
    assert not (tmp_path / "out.json").exists()


def test_without_commit_git_is_asked_and_a_failure_is_refused(cfg, tmp_path, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setattr(exporting.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert export(cfg, tmp_path, commit=False) == 2 and "give --commit" in capsys.readouterr().err
