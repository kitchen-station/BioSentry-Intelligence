"""The fine-tuning run log builder. Standard library only."""

import hashlib
import json
import struct
import types
from pathlib import Path

import pytest

from research.agent import export as exporting
from research.agent_tuning import generate, runlog, tasks
from research.agent_tuning.common import group, read

SMALL = {"planner": (30, 10), "question": (90, 30)}
AGENT = Path(__file__).resolve().parents[2] / "training" / "agent"
ADAPTER = b"adapter bytes for the test"
SECONDS = [1.5, 2.5, 3.0, 1.0]


def lines(path):
    return [json.loads(x) for x in Path(path).read_text().splitlines()]


def write_lines(path, rows):
    Path(path).write_text("".join(json.dumps(r) + "\n" for r in rows))


def write_meta(path, adapter_sha, data_sha, task, n, per_group=1):
    meta = {"adapter_sha256": adapter_sha, "data_sha256": data_sha, "task": task, "per_group": per_group, "examples": n}
    Path(str(path) + ".meta.json").write_text(json.dumps(meta))


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    """A tiny run folder: 3 question steps and 1 planner step, a data folder, a weights folder and answers."""
    root = tmp_path_factory.mktemp("made")
    data, run, model, ev = root / "data", root / "run", root / "model", root / "eval"
    generate.write(data, SMALL)
    train = lines(data / "train.jsonl")
    ids = [e["id"] for e in train if e["task"] == "question"][:3] + [e["id"] for e in train if e["task"] == "planner"][:1]
    (run / "adapter").mkdir(parents=True)
    ev.mkdir()
    model.mkdir()
    (run / "run.json").write_text(json.dumps({
        "examples": 6, "per_group": 2, "epochs": 1, "lr": 0.0002, "max_len": 1536, "longest_example_tokens": 900,
        "seed": 7, "lora": {"r": 8}, "trainable_parameters": 12, "mindspore": "2.7.0", "peft": "9.9.9",
        "ids": ids}))
    write_lines(run / "log.jsonl", [{"step": i + 1, "epoch": 0, "id": ids[i], "tokens": 100 + i, "loss": 0.5 - i / 10,
                                     "seconds": SECONDS[i]} for i in range(4)])
    (run / "adapter" / "adapter_model.safetensors").write_bytes(ADAPTER)
    (model / "weights.bin").write_bytes(b"12345")
    (model / "SHA256SUMS.txt").write_text("ab" * 32 + "  weights.bin\n")
    q = read(data / "heldout.jsonl", 1, "question")
    p = read(data / "heldout.jsonl", 1, "planner")
    # untuned answers everything: the label for planner, a refusal for every question; tuned is short (2 answers)
    write_lines(ev / "untuned-question.jsonl", [{"id": e["id"], "output": tasks.REFUSE} for e in q])
    write_lines(ev / "untuned-planner.jsonl", [{"id": e["id"], "output": e["expected"]} for e in p])
    write_lines(ev / "tuned-question.jsonl", [{"id": e["id"], "output": e["expected"]} for e in q[:2]])
    held = hashlib.sha256((data / "heldout.jsonl").read_bytes()).hexdigest()
    for name, task, n in (("untuned", "question", len(q)), ("untuned", "planner", len(p)), ("tuned", "question", len(q))):
        write_meta(ev / f"{name}-{task}.jsonl", None if name == "untuned" else hashlib.sha256(ADAPTER).hexdigest(), held, task, n)
    args = dict(run_id="t1", train_url=run, data_url=data, model=model, model_name="m/m", model_revision="rev",
                model_licence="Test-Licence-1", evals={"untuned": str(ev), "tuned": str(ev)}, question_per_group=1, planner_per_group=1,
                hardware="a laptop", commit="abc1234", adapter_location="~/somewhere")
    return types.SimpleNamespace(args=args, data=data, q=q, p=p, ids=ids, train=train)


def test_run_log_has_the_hand_checked_values(made):
    doc = runlog.build(**made.args)
    assert doc["format_version"] == 1 and doc["synthetic"] is True and doc["run_id"] == "t1"
    assert doc["base_model"]["files"] == [{"bytes": 5, "name": "weights.bin", "sha256": "ab" * 32}]
    assert doc["base_model"]["licence"] == "Test-Licence-1"
    assert doc["adapter"]["adapter_model.safetensors"] == {"bytes": len(ADAPTER), "sha256": hashlib.sha256(ADAPTER).hexdigest()}
    assert doc["adapter"]["location"] == "~/somewhere" and doc["adapter"]["lora"] == {"r": 8}
    by_id = {e["id"]: e for e in made.train}
    expected = {}
    for i in made.ids[:3]:
        expected["question/" + by_id[i]["category"]] = expected.get("question/" + by_id[i]["category"], 0) + 1
    expected["planner/" + by_id[made.ids[3]]["label"]["tool"]] = 1
    assert doc["data"]["trained_on"] == dict(sorted(expected.items())) and sum(doc["data"]["trained_on"].values()) == 4
    assert doc["training"] == {"planned_steps": 6, "steps_done": 4, "stopped_by_plan": False, "stopped_reason": None,
                               "complete": False, "control": None}
    assert doc["data"]["seed"] == generate.SEED and all(f["matches_file"] for f in doc["data"]["files"].values())
    assert doc["wall_seconds_in_steps"] == 8.0 and doc["wall_time"] == {"seconds": 8.0, "source": "sum of step seconds"}
    assert [r[:3] for r in doc["steps"]] == [[1, "q", 100], [2, "q", 101], [3, "q", 102], [4, "p", 103]]
    assert doc["framework"]["versions"]["peft"] == "9.9.9" and doc["framework"]["sources"]["peft"] == "run.json"
    assert doc["framework"]["sources"]["numpy"] == "requirements pins"
    assert doc["times"]["source"] == "file times"
    sub = doc["data"]["held_out_subsample"]
    assert sum(sub["question"].values()) == len(made.q) and sum(sub["planner"].values()) == len(made.p)
    assert all(v == 1 for v in sub["question"].values()) and all(group(e) in sub["planner"] for e in made.p)


def test_evaluations_are_complete_or_say_how_short_they_are(made):
    ev = runlog.build(**made.args)["evaluations"]
    nq, npl = len(made.q), len(made.p)
    sha = hashlib.sha256(ADAPTER).hexdigest()
    assert ev["question"]["tuned"] == {"answers": 2, "checkpoint_step": 4, "complete": False, "of": nq,
                                       "checked": True, "adapter_sha256": sha}
    assert ev["question"]["untuned"]["adapter_sha256"] is None and ev["question"]["untuned"]["checked"] is True
    assert "checked" not in ev["question"]["perfect"]
    assert "scores" not in ev["question"]["tuned"]
    assert ev["planner"]["tuned"]["complete"] is False and ev["planner"]["tuned"]["answers"] == 0
    assert ev["planner"]["tuned"]["checked"] is False  # no answers file, so nothing to tie to the adapter
    untuned = ev["question"]["untuned"]
    negatives = sum("refuse" in e["label"] for e in made.q)
    assert untuned["complete"] and untuned["checkpoint_step"] == 0 and untuned["scores"]["n"] == nq
    assert untuned["scores"]["refused_ok"] == negatives and untuned["scores"]["wrongly_refused"] == nq - negatives
    assert ev["planner"]["untuned"]["scores"]["exact"] == npl  # the answers are the labels
    assert ev["question"]["perfect"]["scores"]["exact"] == nq - negatives
    assert ev["question"]["matcher"]["scores"]["n"] == nq and "matcher" not in ev["planner"]


def test_examples_carry_inputs_outputs_and_guard_decisions(made):
    doc = runlog.build(**made.args)
    rows = {r["id"]: r for r in doc["examples"]}
    assert len(rows) == len(made.q) + len(made.p)
    for e in made.q:
        r = rows[e["id"]]
        assert r["input"] == e["question"] and r["expected"] == e["expected"] and r["untuned_output"] == tasks.REFUSE
        assert r["untuned_guard"] == "refused" and r["matcher"] == e["matcher"]
    answered = {e["id"] for e in made.q[:2]}
    for e in made.q:
        assert (rows[e["id"]]["tuned_output"] is not None) == (e["id"] in answered)
        if e["id"] not in answered:
            assert rows[e["id"]]["tuned_guard"] is None
    for e in made.p:
        r = rows[e["id"]]
        assert r["input"] == e["messages"][1]["content"] and r["untuned_guard"] == "acted: " + e["label"]["tool"]
        assert r["tuned_output"] is None


def test_the_guard_text_for_acted_and_fell_back(made):
    p = made.p[0]
    assert runlog._guard(p, "not json") == "fell back"
    positive = next(e for e in made.q if "refuse" not in e["label"] and not e["guard_refuses"])
    got = runlog._guard(positive, positive["expected"])
    assert got.startswith("acted: " + positive["label"]["template"]) or got == "refused"  # the matcher may veto
    assert runlog._guard(positive, "not json") == "refused"


def test_same_inputs_give_the_same_bytes(made):
    a, b = runlog.dump(runlog.build(**made.args)), runlog.dump(runlog.build(**made.args))
    assert a == b and a.endswith(b"\n") and json.loads(a)["run_id"] == "t1"


def test_a_run_log_over_the_size_limit_is_refused(made, monkeypatch, capsys):
    monkeypatch.setattr(runlog, "MAX_BYTES", 100)
    assert "over 100" in refused(made.args, capsys)


def test_a_missing_sums_file_is_said(made, tmp_path):
    (tmp_path / "w.bin").write_bytes(b"x")
    got = runlog.build(**{**made.args, "model": tmp_path})["base_model"]
    assert got["files"] == [] and "SHA256SUMS.txt" in got["sha256_note"]


# --- the new run log behaviour ---------------------------------------------------------------------------------

def refused(args, capsys):
    with pytest.raises(SystemExit) as e:
        runlog.dump(runlog.build(**args))
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("refused: ") and err.count("\n") == 1
    return err


def test_a_stopped_run_says_so_and_counts_only_the_log_used(made, tmp_path):
    log = tmp_path / "short.jsonl"
    write_lines(log, lines(made.args["train_url"] / "log.jsonl")[:2])
    doc = runlog.build(**{**made.args, "log": log, "stopped": "fixed for time"})
    assert doc["training"] == {"planned_steps": 6, "steps_done": 2, "stopped_by_plan": True,
                               "stopped_reason": "fixed for time", "complete": False, "control": None}
    assert sum(doc["data"]["trained_on"].values()) == 2 and sum(doc["data"]["planned"].values()) == 4
    assert doc["evaluations"]["question"]["tuned"]["checkpoint_step"] == 2 and len(doc["steps"]) == 2
    assert doc["wall_seconds_in_steps"] == 4.0


def test_a_run_that_did_all_its_steps_is_complete(made, tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "run.json").write_text(json.dumps({**json.loads((made.args["train_url"] / "run.json").read_text()), "examples": 4,
                                              "device": "GPU"}))
    (run / "log.jsonl").write_text((made.args["train_url"] / "log.jsonl").read_text())
    (run / "run-end.json").write_text(json.dumps({"wall_seconds": 99.5, "adapter_b_mean_abs": 0.25}))
    (run / "adapter").mkdir()
    (run / "adapter" / "adapter_model.safetensors").write_bytes(ADAPTER)
    doc = runlog.build(**{**made.args, "train_url": run, "evals": {}, "device": "NPU"})
    assert doc["training"]["complete"] is True and doc["wall_time"] == {"seconds": 99.5, "source": "run-end.json"}
    assert doc["adapter"]["b_mean_abs_from_run_end"] == 0.25
    assert doc["framework"]["backend"] == "MindSpore (GPU) through MindNLP"  # run.json wins over --device
    assert runlog.build(**{**made.args, "evals": {}, "device": "NPU"})["framework"]["backend"] == "MindSpore (NPU) through MindNLP"


def test_the_adapter_and_log_arguments_choose_other_files(made, tmp_path):
    other = tmp_path / "adapter-step2"
    other.mkdir()
    (other / "adapter_model.safetensors").write_bytes(b"other")
    doc = runlog.build(**{**made.args, "adapter_dir": other, "evals": {}})
    assert doc["adapter"]["adapter_model.safetensors"]["sha256"] == hashlib.sha256(b"other").hexdigest()


def test_an_answers_file_tied_to_another_adapter_is_refused(made, tmp_path, capsys):
    ev = tmp_path / "ev"
    ev.mkdir()
    held = hashlib.sha256((made.data / "heldout.jsonl").read_bytes()).hexdigest()
    sha = hashlib.sha256(ADAPTER).hexdigest()
    n = len(made.q)
    write_lines(ev / "tuned-question.jsonl", [{"id": e["id"], "output": "{}"} for e in made.q])
    args = {**made.args, "evals": {"tuned": str(ev)}}
    assert "tuned-question.jsonl.meta.json" in refused(args, capsys)  # no meta file yet
    for adapter_sha, data_sha, task, count, key in ((sha[::-1], held, "question", n, "adapter_sha256"),
                                                    (None, held, "question", n, "adapter_sha256"),
                                                    (sha, "0" * 64, "question", n, "data_sha256"),
                                                    (sha, held, "planner", n, "task"),
                                                    (sha, held, "question", n + 1, "examples")):
        write_meta(ev / "tuned-question.jsonl", adapter_sha, data_sha, task, count)
        assert key in refused(args, capsys)
    write_meta(ev / "tuned-question.jsonl", sha, held, "question", n, per_group=2)
    assert "per_group" in refused(args, capsys)
    write_meta(ev / "tuned-question.jsonl", sha, held, "question", n)
    assert runlog.build(**args)["evaluations"]["question"]["tuned"]["complete"] is True


def test_an_untuned_answers_file_must_have_no_adapter(made, tmp_path, capsys):
    ev = tmp_path / "ev"
    ev.mkdir()
    held = hashlib.sha256((made.data / "heldout.jsonl").read_bytes()).hexdigest()
    write_lines(ev / "untuned-question.jsonl", [{"id": e["id"], "output": "{}"} for e in made.q])
    write_meta(ev / "untuned-question.jsonl", "ab" * 32, held, "question", len(made.q))
    assert "adapter_sha256" in refused({**made.args, "evals": {"untuned": str(ev)}}, capsys)


def test_a_missing_meta_file_is_refused_in_one_line(made, tmp_path, capsys):
    ev = tmp_path / "ev"
    ev.mkdir()
    write_lines(ev / "untuned-question.jsonl", [])
    assert "untuned-question.jsonl.meta.json" in refused({**made.args, "evals": {"untuned": str(ev)}}, capsys)


def control_rows(made, losses, wrong_id=False):
    log = lines(made.args["train_url"] / "log.jsonl")
    rows = [{**r, "loss": loss} for r, loss in zip(log, losses)]
    if wrong_id:
        rows[2]["id"] = "question-train-0000"
    return rows


def test_the_control_loss_is_stored_next_to_the_trained_loss(made, tmp_path):
    ctl = tmp_path / "control.jsonl"
    write_lines(ctl, control_rows(made, [0.5, 0.5, 0.25, 0.0]))  # trained: 0.5, 0.4, 0.3, 0.2
    doc = runlog.build(**{**made.args, "control_log": ctl})
    assert [r[5] for r in doc["steps"]] == [0.5, 0.5, 0.25, 0.0]
    assert doc["training"]["control"] == {"steps_compared": 4, "after_step_1": 3, "trained_below_control": 1,
                                          "mean_trained_minus_control": 0.05}  # (-0.1 + 0.05 + 0.2) / 3


def test_a_control_log_with_other_ids_is_refused(made, tmp_path, capsys):
    ctl = tmp_path / "control.jsonl"
    write_lines(ctl, control_rows(made, [0.5] * 4, wrong_id=True))
    assert "question-train-0000" in refused({**made.args, "control_log": ctl}, capsys)
    write_lines(ctl, control_rows(made, [0.5] * 4)[:2])
    assert "step 3" in refused({**made.args, "control_log": ctl}, capsys)


def tiny_safetensors(path, tensors, dtype="F32"):
    """tensors: name -> list of floats. The format: 8 bytes header length, a JSON header, then the raw data."""
    header, data = {"__metadata__": {"format": "pt"}}, b""
    for name, values in tensors.items():
        raw = struct.pack(f"<{len(values)}f", *values)
        header[name] = {"dtype": dtype, "shape": [len(values)], "data_offsets": [len(data), len(data) + len(raw)]}
        data += raw
    blob = json.dumps(header).encode()
    path.write_bytes(struct.pack("<Q", len(blob)) + blob + data)


def test_safetensors_are_read_with_the_standard_library(tmp_path):
    f = tmp_path / "a.safetensors"
    tiny_safetensors(f, {"x.lora_A.weight": [1.0, -3.0], "x.lora_B.weight": [0.0, 0.0], "y.lora_B.weight": [0.5, -0.5, 0.0, 1.0],
                         "base.weight": [9.0]})
    got = runlog.safetensors_stats(f)
    assert got == {"parsed": True, "lora_A": {"tensors": 1, "not_all_zero": 1, "mean_abs": 2.0},
                   "lora_B": {"tensors": 2, "not_all_zero": 1, "mean_abs": 0.333333333}}  # 2.0 / 6


def test_an_unreadable_adapter_is_said_not_guessed(tmp_path):
    f = tmp_path / "bad.safetensors"
    f.write_bytes(b"not a safetensors file")
    assert runlog.safetensors_stats(f)["parsed"] is False
    tiny_safetensors(f, {"a.lora_B.weight": [1.0]}, dtype="BF16")
    assert "BF16" in runlog.safetensors_stats(f)["reason"]
    assert runlog.safetensors_stats(tmp_path / "missing")["parsed"] is False


def test_the_run_log_records_the_adapter_weights(made, tmp_path):
    other = tmp_path / "adapter"
    other.mkdir()
    tiny_safetensors(other / "adapter_model.safetensors", {"a.lora_A.weight": [2.0], "a.lora_B.weight": [0.0]})
    got = runlog.build(**{**made.args, "adapter_dir": other, "evals": {}})["adapter"]["weights"]
    assert got["lora_B"]["not_all_zero"] == 0 and got["lora_A"]["mean_abs"] == 2.0
    assert runlog.build(**made.args)["adapter"]["weights"]["parsed"] is False  # the fixture's adapter is not a real file


def test_no_run_log_holds_a_path_or_a_user_name():
    for f in (AGENT / "runs").glob("*"):
        text = f.read_text()
        assert "/Users/" not in text and "/home/" not in text and str(Path.home()) not in text, f.name


def scored_doc(made, tmp_path, untuned, tuned):
    """A run log whose two question runs hold these outputs (each a function of the example), and the question examples."""
    ev = tmp_path / "ev"
    ev.mkdir()
    held = hashlib.sha256((made.data / "heldout.jsonl").read_bytes()).hexdigest()
    for name, fn in (("untuned", untuned), ("tuned", tuned)):
        rows = [{"id": e["id"], "output": fn(e)} for e in made.q if fn(e) is not None]
        write_lines(ev / f"{name}-question.jsonl", rows)
        write_meta(ev / f"{name}-question.jsonl", None if name == "untuned" else hashlib.sha256(ADAPTER).hexdigest(),
                   held, "question", len(made.q))
    return runlog.build(**{**made.args, "evals": {"untuned": str(ev), "tuned": str(ev)}})


def letting_through(made):
    """Questions whose expected answer the guard lets through (the keyword matcher may veto some)."""
    return [e for e in made.q if tasks.guard_question(e["expected"], e["question"])[0]]


def test_scores_file_counts_are_hand_checked_and_the_export_accepts_it(made, tmp_path):
    good = {e["id"] for e in letting_through(made)}
    assert good
    doc = scored_doc(made, tmp_path, lambda e: tasks.REFUSE, lambda e: e["expected"] if e["id"] in good else tasks.REFUSE)
    calls = []
    out = runlog.scores_file(doc, made.q, lambda template, args: calls.append(template) or template == "news2_threshold")
    s = out["scores"]
    before, after = s["runs"]
    assert s["n"] == len(made.q) and (before["label"], after["label"]) == ("before", "after")
    assert (before["answered"], before["grounding_passed"], before["correct_template"]) == (0, 0, 0)
    assert after["answered"] == len(good) and after["correct_template"] == len(good)
    assert after["grounding_passed"] == sum(e["label"]["template"] == "news2_threshold" for e in letting_through(made))
    for r in s["runs"]:
        assert r["answered"] + r["refused"] == s["n"]
    assert before["model"] == "m/m" and after["model"] == "m/m + LoRA adapter " + hashlib.sha256(ADAPTER).hexdigest()[:12]
    assert s["question_set"].startswith("blind held-out questions, first 1 of each group (%d questions; heldout.jsonl sha256 " % len(made.q))
    assert out["notes"] == runlog.SCORE_NOTES and len(calls) <= len(good)
    exporting.check_scores(out)


def test_each_distinct_experiment_runs_once_across_both_runs(made, tmp_path):
    good = {e["id"] for e in letting_through(made)}
    keep = lambda e: e["expected"] if e["id"] in good else tasks.REFUSE  # noqa: E731
    doc = scored_doc(made, tmp_path, keep, keep)
    calls = []
    out = runlog.scores_file(doc, made.q, lambda template, args: calls.append(json.dumps(args, sort_keys=True)) or True)
    before, after = out["scores"]["runs"]
    assert len(calls) == len(set(calls)) and 0 < len(calls) <= len(good)
    assert before["answered"] == after["answered"] == len(good) and before["grounding_passed"] == after["grounding_passed"]


def test_an_incomplete_run_writes_no_scores(made, tmp_path, capsys):
    doc = scored_doc(made, tmp_path, lambda e: tasks.REFUSE, lambda e: None)
    with pytest.raises(SystemExit) as stop:
        runlog.scores_file(doc, made.q, lambda template, args: pytest.fail("no experiment may run"))
    assert stop.value.code == 2 and "incomplete" in capsys.readouterr().err


def test_the_real_experiment_runner_asks_the_tool_what_ask_would_and_passes_a_grounded_answer(tmp_path, monkeypatch):
    from test_agent_ask import cfg as _unused  # noqa: F401
    from biosentry.domain.scoring.tier_b.baseline import DEFAULTS
    from research.agent import ask, ledger, tools
    from test_agent_tools import cfg_for, toy_trials

    monkeypatch.setattr(tools.evaluate, "_baseline_params", lambda: (DEFAULTS, "test"))
    cfg = cfg_for(tmp_path)
    toy_trials(cfg, range(1, 11))
    cfg.models.mkdir()
    question = "What if we alerted at a NEWS2 total of 5 or more?"
    ask.ask(cfg, question)  # what ask builds is in its ledger step
    asked = ledger.read(cfg)[1]["calls"][0]["args"]
    seen, real = [], tools.call
    monkeypatch.setattr(tools, "call", lambda c, name, args, *a, **k: seen.append((name, args)) or real(c, name, args, *a, **k))
    monkeypatch.setattr(runlog.Config, "from_env", classmethod(lambda cls, env=None: cfg))
    args = {"dataset": "ward-v2", "seeds": list(range(1, 11)), "window": "early",
            "rules": ask._rules("news2_threshold", 5, question)}
    assert runlog.run_experiment("news2_threshold", args) is True
    assert seen == [("evaluate_rules", asked)] and asked == args
    assert runlog.run_experiment("alerts_per_nurse", {**args, "rules": [{"rule": "bogus"}]}) is False  # a tool error
