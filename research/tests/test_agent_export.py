import json

import pytest

from research.agent import __main__ as cli
from research.agent import ask, ledger
from test_agent_ask import cfg  # noqa: F401  (ten toy trials as tuning seeds)
from test_agent_ledger import tree_hash
from test_agent_planner import env_for
from test_agent_tools import make_run


def test_export_lists_questions_rounds_and_proposals_and_changes_nothing(cfg, tmp_path, capsys):  # noqa: F811
    ask.ask(cfg, "What if we alerted at a NEWS2 total of 5 or more?")
    ask.ask(cfg, "What is the capital of France?")
    sid = ledger.begin_step(cfg, "round", "goal", {"planner": "rule", "rule": "R5", "experiment": "sweep_baseline",
                                                   "tool": "sweep_baseline", "args": {}})
    ledger.add_step(cfg, sid, [{"tool": "sweep_baseline", "args": {}, "ok": True, "result": {}}], "done")
    make_run(cfg, sid)
    pid = ledger.propose(cfg, "adopt_reward_policy", sid, f"runs/{sid}", False, {})
    ledger.begin_step(cfg, "round", "goal", {"experiment": "reward_round", "tool": "run_reward_round"})  # never finished
    before_ledger, before_models = (cfg.home / "ledger.jsonl").read_bytes(), tree_hash(cfg.models)
    out = tmp_path / "agent.json"
    assert cli.main(["export", str(out)], env_for(cfg)) == 0
    assert capsys.readouterr().out.strip() == "exported 2 questions, 2 rounds, 1 proposals"
    data = json.loads(out.read_text())
    assert (data["format_version"], data["synthetic"], data["validated"]) == (1, True, False)
    assert data["note"] == ask.LAST_LINE and data["ledger_records"] == len(ledger.read(cfg))
    q1, q2 = data["questions"]
    assert q1["question"].startswith("What if we alerted") and (q1["experiment"], q1["threshold"]) == ("news2_threshold", 5)
    assert q1["answered"] is True and q1["grounding"] == "ok" and q1["answer"].endswith(ask.LAST_LINE)
    assert "20 of 20" in q1["answer"] and q1["started_at"] <= q1["finished_at"]
    assert (q2["experiment"], q2["threshold"], q2["answered"], q2["answer"], q2["tool_failed"]) == (None, None, False, None, False)
    r1, r2 = data["rounds"]
    assert (r1["experiment"], r1["planner"], r1["rule"], r1["ok"], r1["conclusion"]) == ("sweep_baseline", "rule", "R5", True, "done")
    assert (r2["experiment"], r2["finished_at"], r2["ok"], r2["conclusion"]) == ("reward_round", None, None, None)
    p, = data["proposals"]
    assert {k: p[k] for k in p if k != "proposed_at"} == {
        "id": pid, "action": "adopt_reward_policy", "step": sid, "state": "proposed", "files": 3,
        "approved_by": None, "approved_at": None, "applied_at": None}
    assert p["proposed_at"] >= r1["finished_at"]
    # read only: the ledger and the models folder are byte for byte what they were
    assert (cfg.home / "ledger.jsonl").read_bytes() == before_ledger and tree_hash(cfg.models) == before_models
    ledger.approve(cfg, pid, "Ada Lovelace")
    ledger.apply(cfg, pid)
    assert cli.main(["export", str(out)], env_for(cfg)) == 0
    p = json.loads(out.read_text())["proposals"][0]
    assert (p["state"], p["approved_by"]) == ("applied", "Ada Lovelace") and p["approved_at"] <= p["applied_at"]


def test_export_refuses_the_models_folder_and_a_broken_ledger(cfg, tmp_path, capsys):  # noqa: F811
    assert cli.main(["export", str(cfg.models / "agent.json")], env_for(cfg)) == 2
    assert "does not write inside the models folder" in capsys.readouterr().err
    assert cli.main(["export"], env_for(cfg)) == 2
    ask.ask(cfg, "What is the capital of France?")
    path = cfg.home / "ledger.jsonl"
    path.write_bytes(path.read_bytes().replace(b"capital", b"capitol", 1))
    assert cli.main(["export", str(tmp_path / "x.json")], env_for(cfg)) == 2
    assert "broken" in capsys.readouterr().err and not (tmp_path / "x.json").exists()


def test_a_question_with_markup_is_exported_as_plain_text(cfg, tmp_path):  # noqa: F811
    ask.ask(cfg, '<script>alert(1)</script> "quoted" \x1b[31m threshold of 5?')
    out = tmp_path / "a.json"
    ledger.write_export(cfg, str(out))
    q, = json.loads(out.read_text())["questions"]
    assert q["question"].startswith("<script>alert(1)</script>") and "\x1b" not in q["question"]


@pytest.mark.parametrize("field", ["questions", "rounds", "proposals"])
def test_an_empty_ledger_exports_empty_lists(tmp_path, field):
    from test_agent_tools import cfg_for

    data = ledger.export(ledger.read(cfg_for(tmp_path)))
    assert data[field] == [] and data["ledger_records"] == 0
