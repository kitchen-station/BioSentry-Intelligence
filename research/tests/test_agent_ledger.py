import hashlib
import re
from pathlib import Path

import pytest

import research.agent
from research.agent import __main__ as cli
from research.agent import ledger
from research.agent.ledger import LedgerError
from test_agent_tools import cfg_for, make_run


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")) if root.exists() else []:
        h.update(str(p.relative_to(root)).encode() + (p.read_bytes() if p.is_file() else b"dir"))
    return h.hexdigest()


def step(cfg, text="goal"):
    sid = ledger.begin_step(cfg, "round", text, {"tool": "x"})
    return ledger.add_step(cfg, sid, [], "done")["id"]


def lines(cfg):
    return (cfg.home / "ledger.jsonl").read_bytes().split(b"\n")[:-1]


@pytest.fixture
def cfg(tmp_path):
    return cfg_for(tmp_path)


def proposed(cfg, run="S-0001", nt=False):
    """A step, a run folder with a policy, and a proposal to adopt it."""
    make_run(cfg, run, nt)
    return ledger.propose(cfg, "adopt_reward_policy", step(cfg), f"runs/{run}", nt, {"alerts": 7})


# --- the ledger -----------------------------------------------------------------------------------------

def test_append_chains_records_and_keeps_earlier_bytes(cfg, monkeypatch):
    monkeypatch.setattr(ledger, "now", lambda: "2026-11-13T10:00:00Z")
    step(cfg, "first")
    before = (cfg.home / "ledger.jsonl").read_bytes()
    ledger.add_fallback(cfg, "bad_json", "api.example.com", "text")
    step(cfg, "third")
    after = (cfg.home / "ledger.jsonl").read_bytes()
    assert after.startswith(before)
    recs = ledger.read(cfg)
    assert [r["seq"] for r in recs] == [1, 2, 3, 4, 5] and recs[0]["prev"] == "0" * 64
    assert [r["type"] for r in recs] == ["start", "step", "planner_fallback", "start", "step"]
    assert recs[1]["prev"] == hashlib.sha256(lines(cfg)[0]).hexdigest()
    assert [r["id"] for r in recs if r["type"] == "step"] == ["S-0001", "S-0004"]
    assert recs[0]["at"] == "2026-11-13T10:00:00Z" and recs[0]["goal"] == "first"


def test_a_hand_edited_line_is_caught_with_its_sequence_number(cfg):
    for _ in range(3):
        step(cfg)
    path = cfg.home / "ledger.jsonl"
    path.write_bytes(path.read_bytes().replace(b'"conclusion":"done"', b'"conclusion":"edit"', 1))
    with pytest.raises(LedgerError, match="broken at seq 3"):  # the line after the edit no longer matches its prev
        ledger.read(cfg)
    with pytest.raises(LedgerError, match="broken at seq 3"):  # and nothing can be appended
        step(cfg)


def test_a_deleted_middle_line_and_a_cut_tail_are_caught(cfg):
    for _ in range(3):
        step(cfg)
    path, data = cfg.home / "ledger.jsonl", (cfg.home / "ledger.jsonl").read_bytes()
    first, _, third = lines(cfg)[:3]
    path.write_bytes(first + b"\n" + third + b"\n")
    with pytest.raises(LedgerError, match="broken at seq 2"):
        ledger.read(cfg)
    path.write_bytes(data[:-5])
    with pytest.raises(LedgerError, match="incomplete"):
        ledger.read(cfg)


def test_a_step_needs_its_own_start_and_closes_it_once(cfg):
    with pytest.raises(LedgerError, match="no open step"):
        ledger.add_step(cfg, "S-0001", [], "c")
    sid = ledger.begin_step(cfg, "round", "g", {"tool": "x"})
    ledger.add_fallback(cfg, "timeout", "api.example.com")  # another record arriving mid-run is fine
    rec = ledger.add_step(cfg, sid, [], "c")
    assert (rec["id"], rec["seq"], rec["plan"], rec["goal"]) == ("S-0001", 3, {"tool": "x"}, "g")
    with pytest.raises(LedgerError, match="no open step"):
        ledger.add_step(cfg, sid, [], "again")
    with pytest.raises(LedgerError, match="bad step"):
        ledger.begin_step(cfg, "other", "g", {})


def test_untrusted_text_is_capped_and_cleaned_and_the_model_key_never_gets_in(tmp_path):
    cfg = cfg_for(tmp_path, model_key="sk-secret-123")
    rec = ledger.add_step(cfg, ledger.begin_step(cfg, "ask", "a\x1b[31m" + "z" * 900, {}), [], "ok", "ok")
    assert rec["question"].startswith("a?[31m") and len(rec["question"]) == 500
    with pytest.raises(LedgerError, match="model key"):
        ledger.add_fallback(cfg, "bad_json", "api.example.com", "sk-secret-123")
    assert "sk-secret-123" not in (cfg.home / "ledger.jsonl").read_text()
    with pytest.raises(LedgerError, match="256 KiB"):
        ledger._append(cfg, "step", {"big": "x" * ledger.MAX_LINE})


def test_a_symlinked_ledger_is_refused(tmp_path, cfg):
    cfg.home.mkdir()
    (tmp_path / "other").write_text("")
    (cfg.home / "ledger.jsonl").symlink_to(tmp_path / "other")
    with pytest.raises(LedgerError, match="cannot open the ledger"):
        step(cfg)
    assert (tmp_path / "other").read_text() == ""


# --- proposals ------------------------------------------------------------------------------------------

def test_a_proposal_lists_hashed_sources_and_fixed_targets(cfg):
    pid = proposed(cfg)
    rec = [r for r in ledger.read(cfg) if r["type"] == "proposal"][0]
    base = cfg.home / "runs" / "S-0001" / "reward_policy"
    assert pid == "P-0001" and rec["step"] == "S-0001" and rec["evidence"] == {"alerts": 7}
    assert [(f["source"], f["target"]) for f in rec["files"]] == [
        ("runs/S-0001/reward_policy/policy.json", "tier_b/reward_policy/policy.json"),
        ("runs/S-0001/reward_policy/generations/gen-00.json", "tier_b/reward_policy/generations/gen-00.json"),
        ("runs/S-0001/reward_policy/generations/gen-01.json", "tier_b/reward_policy/generations/gen-01.json")]
    assert rec["files"][0]["sha256"] == hashlib.sha256((base / "policy.json").read_bytes()).hexdigest()
    assert "status" not in rec and ledger.status(ledger.read(cfg), pid) == "proposed"


def test_the_no_temperature_policy_goes_to_its_own_target(cfg):
    proposed(cfg, nt=True)
    rec = [r for r in ledger.read(cfg) if r["type"] == "proposal"][0]
    assert rec["files"][0]["target"] == "tier_b/reward_policy/no_temperature/policy.json"


def test_a_baseline_params_proposal_maps_to_one_target(cfg):
    sid = step(cfg)
    (cfg.home / "runs" / "S-0001").mkdir(parents=True)
    (cfg.home / "runs" / "S-0001" / "params.json").write_text('{"k": 2.0, "p": 3}\n')
    ledger.propose(cfg, "set_baseline_params", sid, "runs/S-0001", False, {})
    rec = ledger.read(cfg)[-1]
    assert [(f["source"], f["target"]) for f in rec["files"]] == [
        ("runs/S-0001/params.json", "tier_b/baseline/params.json")]


def test_a_second_pending_proposal_for_the_same_target_is_refused(cfg):
    proposed(cfg)
    with pytest.raises(LedgerError, match="already proposes this"):
        ledger.propose(cfg, "adopt_reward_policy", "S-0001", "runs/S-0001", False, {})


def test_a_pending_proposal_blocks_another_that_shares_any_target(cfg):
    proposed(cfg, "S-0001")
    make_run(cfg, "S-0003")
    (cfg.home / "runs" / "S-0003" / "reward_policy" / "generations" / "gen-01.json").unlink()  # other file set
    with pytest.raises(LedgerError, match="P-0001 already proposes this"):
        ledger.propose(cfg, "adopt_reward_policy", "S-0001", "runs/S-0003", False, {})


@pytest.mark.parametrize("action, run, step_id, why", [
    ("write_anything", "runs/S-0001", "S-0001", "bad proposal"),
    ("adopt_reward_policy", "runs/../x", "S-0001", "bad proposal"),
    ("adopt_reward_policy", "runs/S-0001", "S-0099", "no step"),
])
def test_a_bad_proposal_is_refused(cfg, action, run, step_id, why):
    step(cfg)
    make_run(cfg)
    with pytest.raises(LedgerError, match=why):
        ledger.propose(cfg, action, step_id, run, False, {})


def test_a_proposal_for_a_tampered_or_symlinked_source_is_refused(cfg, tmp_path):
    sid = step(cfg)
    make_run(cfg)
    policy = cfg.home / "runs" / "S-0001" / "reward_policy" / "policy.json"
    policy.write_text(policy.read_text().replace('"theta": 3.0', '"theta": 9.0'))
    with pytest.raises(LedgerError, match="sha256"):
        ledger.propose(cfg, "adopt_reward_policy", sid, "runs/S-0001", False, {})
    make_run(cfg)
    gen = cfg.home / "runs" / "S-0001" / "reward_policy" / "generations" / "gen-01.json"
    (tmp_path / "x.json").write_text("{}")
    gen.unlink()
    gen.symlink_to(tmp_path / "x.json")
    with pytest.raises(LedgerError, match="outside the agent home|missing or a symlink"):
        ledger.propose(cfg, "adopt_reward_policy", sid, "runs/S-0001", False, {})


# --- approve and apply ----------------------------------------------------------------------------------------

def test_approve_refuses_unknown_bad_name_and_a_second_time(cfg):
    pid = proposed(cfg)
    with pytest.raises(LedgerError, match="no proposal"):
        ledger.approve(cfg, "P-0009", "Ada Lovelace")
    for bad in ("x", "1abc", "a;b", "Ada\n", "A" * 61):
        with pytest.raises(LedgerError, match="--by"):
            ledger.approve(cfg, pid, bad)
    rec = ledger.approve(cfg, pid, "Ada O'Neil-Smith Jr.")
    assert rec["by"] == "Ada O'Neil-Smith Jr." and rec["proposal"] == pid and rec["os_user"]
    assert ledger.status(ledger.read(cfg), pid) == "approved"
    with pytest.raises(LedgerError, match="already approved"):
        ledger.approve(cfg, pid, "Ada Lovelace")


def test_apply_without_approval_is_refused_and_models_is_unchanged(cfg):
    pid = proposed(cfg)
    cfg.models.mkdir()
    (cfg.models / "keep.txt").write_text("x")
    before = tree_hash(cfg.models)
    with pytest.raises(LedgerError, match="not approved"):
        ledger.apply(cfg, pid)
    with pytest.raises(LedgerError, match="no proposal"):
        ledger.apply(cfg, "P-0042")
    assert tree_hash(cfg.models) == before


def test_apply_after_approval_changes_only_the_mapped_targets(cfg):
    pid = proposed(cfg)
    cfg.models.mkdir()
    (cfg.models / "keep.txt").write_text("x")
    (cfg.models / "tier_b" / "reward_policy").mkdir(parents=True)
    old = cfg.models / "tier_b" / "reward_policy" / "policy.json"
    old.write_text("old")
    ledger.approve(cfg, pid, "Ada Lovelace")
    rec = ledger.apply(cfg, pid)
    src = cfg.home / "runs" / "S-0001" / "reward_policy"
    written = sorted(p.relative_to(cfg.models).as_posix() for p in cfg.models.rglob("*") if p.is_file())
    assert written == ["keep.txt", "tier_b/reward_policy/generations/gen-00.json",
                       "tier_b/reward_policy/generations/gen-01.json", "tier_b/reward_policy/policy.json"]
    assert (cfg.models / "keep.txt").read_text() == "x"
    assert old.read_bytes() == (src / "policy.json").read_bytes()
    by_target = {f["target"]: f for f in rec["files"]}
    policy = by_target["tier_b/reward_policy/policy.json"]
    assert policy["sha256_before"] == hashlib.sha256(b"old").hexdigest()
    assert policy["sha256_after"] == hashlib.sha256(old.read_bytes()).hexdigest()
    assert by_target["tier_b/reward_policy/generations/gen-00.json"]["sha256_before"] is None
    assert ledger.status(ledger.read(cfg), pid) == "applied"
    assert not list(cfg.models.rglob("*.tmp"))
    with pytest.raises(LedgerError, match="already applied"):
        ledger.apply(cfg, pid)


def test_a_failed_copy_is_recorded_with_what_was_written_and_can_be_run_again(cfg):
    pid = proposed(cfg)
    ledger.approve(cfg, pid, "Ada Lovelace")
    block = cfg.models / "tier_b" / "reward_policy" / "policy.json.tmp"
    block.mkdir(parents=True)  # the last copy (policy.json) cannot open its temp file
    with pytest.raises(LedgerError, match="2 of 3 files were written and are recorded as apply_failed"):
        ledger.apply(cfg, pid)
    records = ledger.read(cfg)
    assert records[-1]["type"] == "apply_failed" and records[-1]["proposal"] == pid
    assert [f["target"] for f in records[-1]["files"]] == [
        "tier_b/reward_policy/generations/gen-00.json", "tier_b/reward_policy/generations/gen-01.json"]
    assert ledger.status(records, pid) == "approved"
    assert not list(cfg.models.rglob("*.tmp")) or list(cfg.models.rglob("*.tmp")) == [block]
    block.rmdir()
    assert len(ledger.apply(cfg, pid)["files"]) == 3 and ledger.status(ledger.read(cfg), pid) == "applied"


def test_a_ledger_with_no_room_for_the_record_stops_apply_before_any_write(cfg, monkeypatch):
    pid = proposed(cfg)
    ledger.approve(cfg, pid, "Ada Lovelace")
    monkeypatch.setattr(ledger, "MAX_FILE", (cfg.home / "ledger.jsonl").stat().st_size + 100)
    with pytest.raises(LedgerError, match="ledger is full; nothing was written"):
        ledger.apply(cfg, pid)
    assert not cfg.models.exists()


def test_a_forged_proposal_is_refused_by_apply(cfg):
    """Records written past the public functions (the private writer, used here on purpose) still
    cannot move files from two runs, or name a step that is not in the ledger."""
    make_run(cfg, "S-0001")
    make_run(cfg, "S-0002")
    sid = step(cfg)
    sha = lambda rel: hashlib.sha256((cfg.home / rel).read_bytes()).hexdigest()  # noqa: E731
    a, b = "runs/S-0001/reward_policy/policy.json", "runs/S-0002/reward_policy/generations/gen-01.json"
    files = [{"source": s, "sha256": sha(s), "target": ledger._target("adopt_reward_policy", s)} for s in (a, b)]
    for step_id, why in ((sid, "more than one run"), ("S-9999", "its step is not in the ledger")):
        pid = ledger._append(cfg, "proposal", {"action": "adopt_reward_policy", "step": step_id, "files": files,
                                                "evidence": {}})["id"]
        ledger.approve(cfg, pid, "Ada Lovelace")
        with pytest.raises(LedgerError, match=why):
            ledger.apply(cfg, pid)
    assert not cfg.models.exists()


def test_the_planner_the_loop_and_ask_cannot_reach_the_sign_off():
    """approve and apply are commands a person types. No agent module names them or the private writer."""
    folder = Path(research.agent.__file__).parent
    for name in ("planner.py", "loop.py", "ask.py", "tools.py", "config.py"):
        text = (folder / name).read_text()
        assert not re.search(r"ledger\s*\.\s*(approve|apply|_append)\b|\b_append\b|getattr\(", text), name
        assert not re.search(r"import[^\n]*\b(approve|apply)\b", text), name


def test_a_source_changed_after_approval_is_refused_and_nothing_is_written(cfg):
    pid = proposed(cfg)
    ledger.approve(cfg, pid, "Ada Lovelace")
    gen = cfg.home / "runs" / "S-0001" / "reward_policy" / "generations" / "gen-01.json"
    gen.write_text(gen.read_text() + " ")
    with pytest.raises(LedgerError, match="changed since it was approved"):
        ledger.apply(cfg, pid)
    assert not cfg.models.exists()


def test_a_missing_source_and_a_symlinked_target_are_refused_before_any_write(cfg, tmp_path):
    pid = proposed(cfg)
    ledger.approve(cfg, pid, "Ada Lovelace")
    (tmp_path / "away").mkdir()
    cfg.models.mkdir()
    (cfg.models / "tier_b").symlink_to(tmp_path / "away")
    with pytest.raises(LedgerError, match="cannot be written safely"):
        ledger.apply(cfg, pid)
    assert not list((tmp_path / "away").rglob("*"))
    (cfg.models / "tier_b").unlink()
    (cfg.home / "runs" / "S-0001" / "reward_policy" / "generations" / "gen-00.json").unlink()
    with pytest.raises(LedgerError, match="missing or a symlink"):
        ledger.apply(cfg, pid)
    assert not list(cfg.models.rglob("*"))


def test_a_baseline_params_proposal_applies_to_its_one_target(cfg):
    sid = step(cfg)
    (cfg.home / "runs" / "S-0001").mkdir(parents=True)
    (cfg.home / "runs" / "S-0001" / "params.json").write_text('{"k": 2.0, "p": 3}\n')
    pid = ledger.propose(cfg, "set_baseline_params", sid, "runs/S-0001", False, {})
    ledger.approve(cfg, pid, "Ada Lovelace")
    ledger.apply(cfg, pid)
    assert [p.relative_to(cfg.models).as_posix() for p in cfg.models.rglob("*") if p.is_file()] == [
        "tier_b/baseline/params.json"]


# --- the command line ----------------------------------------------------------------------------------------------

def env_for(cfg):
    return {"BIOSENTRY_AGENT_TRIALS": str(cfg.trials), "BIOSENTRY_AGENT_HOME": str(cfg.home),
            "BIOSENTRY_AGENT_MODELS": str(cfg.models)}


def test_status_approve_apply_through_the_command_line(cfg, capsys):
    pid = proposed(cfg)
    env = env_for(cfg)
    assert cli.main(["status"], env) == 0
    assert capsys.readouterr().out.splitlines() == [
        "ledger: 3 records, chain ok", "P-0001 adopt_reward_policy proposed (step S-0001)"]
    assert cli.main(["apply", pid], env) == 2
    assert "refused: P-0001 is proposed, not approved" in capsys.readouterr().err
    assert cli.main(["approve", pid, "--by", "Ada Lovelace"], env) == 0
    assert "approved P-0001 by Ada Lovelace" in capsys.readouterr().out
    assert cli.main(["apply", pid], env) == 0
    assert "applied P-0001: 3 files" in capsys.readouterr().out
    assert (cfg.models / "tier_b" / "reward_policy" / "policy.json").is_file()


@pytest.mark.parametrize("argv", [[], ["nope"], ["status", "x"], ["approve"], ["approve", "P-0001", "--by"],
                                  ["approve", "P-0001", "x"], ["apply", "P-0001", "P-0002"]])
def test_bad_commands_are_one_refused_line_with_exit_2(cfg, capsys, argv):
    assert cli.main(argv, env_for(cfg)) == 2
    err = capsys.readouterr().err
    assert err.startswith("refused: ") and err.count("\n") == 1


def test_a_broken_ledger_is_refused_by_status(cfg, capsys):
    step(cfg)
    step(cfg)
    path = cfg.home / "ledger.jsonl"
    path.write_bytes(path.read_bytes().replace(b'"conclusion":"done"', b'"conclusion":"edit"', 1))
    assert cli.main(["status"], env_for(cfg)) == 2
    assert "broken at seq 3" in capsys.readouterr().err


# --- only the ledger writes under models -----------------------------------------------------------------------------

def test_only_ledger_py_refers_to_the_models_folder():
    # A source check: no other module in the package names `cfg.models` (config.py only declares it).
    folder = Path(research.agent.__file__).parent
    for path in folder.glob("*.py"):
        if path.name != "ledger.py":
            assert "cfg.models" not in path.read_text(), path.name
    assert "cfg.models" in (folder / "ledger.py").read_text()
