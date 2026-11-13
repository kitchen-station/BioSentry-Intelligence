"""Writes one small JSON file that records a LoRA fine-tuning run and how it scored. It never trains.

    python -m research.agent_tuning.runlog --run-id run1 --train_url <run dir> --data_url <data dir> \
        --model <weights dir> --model-name Qwen/Qwen2.5-0.5B-Instruct --model-revision <sha> \
        --model-licence Apache-2.0 --eval untuned=<dir or prefix> --eval tuned=<dir or prefix> \
        --question-per-group 6 --planner-per-group 3 --hardware "<text>" --commit <sha> \
        [--log <file>] [--adapter <dir>] [--control-log <file>] [--stopped "<reason>"] [--device CPU] \
        [--scores-out <file>] --out training/agent/runs/run1.json

Reads: `run.json`, `log.jsonl` (and `run-end.json`, `adapter/`) in the run dir; `train.jsonl`,
`heldout.jsonl`, `manifest.json` in the data dir; `SHA256SUMS.txt` in the weights dir. An --eval value
is a folder holding `<name>-question.jsonl` and `<name>-planner.jsonl`, or a path prefix for the same
two files. An answers file that is missing or short is recorded as incomplete; nothing is invented.
Each answers file that exists needs the `<name>.meta.json` that run_model.py writes beside it, and
its adapter hash, data hash, task, per-group and example count must match, or nothing is written
(exit 2). `--control-log` is the frozen run's log: its loss is stored next to the trained loss.
`--scores-out` also writes the before and after counts for `python -m research.agent export --scores`. It runs
each distinct experiment once on the tuning seeds (needs the trials, like `ask`) and refuses if a run is incomplete.
Same inputs, same bytes: sorted keys, no clock (times come from the run files). Standard library only.
"""

import argparse
import hashlib
import json
import math
import re
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

from research.agent import ask, tools
from research.agent.config import AgentError, Config
from research.agent_tuning import score, tasks
from research.agent_tuning.common import complete_lines, group, read

HERE = Path(__file__).parent
MAX_BYTES = 1_000_000
NOTE = "Generated data, simulated ward, not validated."
PACKAGES = ("mindspore", "mindnlp", "transformers", "peft", "tokenizers", "numpy")
RULE = "the first N examples of each group, in held-out file order (research.agent_tuning.common.read)"


def refuse(message: str):
    print("refused: " + message, file=sys.stderr)
    raise SystemExit(2)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _json(path: Path) -> dict:
    return json.loads(path.read_text())


def _count(examples: list[dict]) -> dict:
    out: dict[str, int] = {}
    for ex in examples:
        out[group(ex)] = out.get(group(ex), 0) + 1
    return dict(sorted(out.items()))


def _base_model(model: Path, name: str, revision: str, licence: str) -> dict:
    sums, files = model / "SHA256SUMS.txt", []
    out = {"name": name, "revision": revision, "licence": licence, "files": files}
    if not sums.is_file():
        out["sha256_note"] = "SHA256SUMS.txt was not found, so no weight hashes are recorded"
        return out
    for line in sums.read_text().splitlines():
        digest, _, fname = line.strip().partition("  ")
        if digest and fname:
            path = model / fname.lstrip("*")
            files.append({"name": fname.lstrip("*"), "bytes": path.stat().st_size if path.is_file() else None,
                          "sha256": digest})
    out["files"] = sorted(files, key=lambda f: f["name"])
    return out


def _pins() -> dict:
    pins = {}
    for line in (HERE / "requirements.txt").read_text().splitlines():
        m = re.fullmatch(r"([A-Za-z0-9_.-]+)==(\S+)", line.strip())
        if m:
            pins[m[1].lower()] = m[2]
    return pins


def _framework(run: dict, device: str) -> dict:
    pins = _pins()
    versions, sources = {}, {}
    for p in PACKAGES:
        if run.get(p):
            versions[p], sources[p] = run[p], "run.json"
        elif p in pins:
            versions[p], sources[p] = pins[p], "requirements pins"
    kinds = set(sources.values())
    return {"backend": f"MindSpore ({run.get('device') or device}) through MindNLP", "versions": versions, "sources": sources,
            "source": kinds.pop() if len(kinds) == 1 else "mixed"}


def safetensors_stats(path: Path) -> dict:
    """lora_A and lora_B facts read with the standard library; says so when the file cannot be parsed (float32 only)."""
    try:
        raw = path.read_bytes()
        (size,) = struct.unpack("<Q", raw[:8])
        header, base = json.loads(raw[8:8 + size]), 8 + size
        out = {"parsed": True}
        for kind in ("lora_A", "lora_B"):
            tensors = {n: t for n, t in header.items() if n != "__metadata__" and kind in n}
            total, count, nonzero = [], 0, 0
            for t in tensors.values():
                if t["dtype"] != "F32":
                    raise ValueError(f"dtype {t['dtype']} is not F32")
                lo, hi = t["data_offsets"]
                values = struct.unpack(f"<{(hi - lo) // 4}f", raw[base + lo:base + hi])
                total.append(math.fsum(abs(v) for v in values))
                count += len(values)
                nonzero += any(values)
            out[kind] = {"tensors": len(tensors), "not_all_zero": nonzero,
                         "mean_abs": round(math.fsum(total) / count, 9) if count else None}
        return out
    except (OSError, ValueError, KeyError, TypeError, struct.error) as e:
        return {"parsed": False, "reason": f"{type(e).__name__}: {e}"}


def _utc(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")


def _times(train: Path, run: dict, adapter: Path) -> dict:
    end = _json(train / "run-end.json") if (train / "run-end.json").is_file() else {}
    start_at, end_at = run.get("started_at"), end.get("finished_at")
    note = []
    if not start_at:
        start_at, note = _utc(train / "run.json"), note + ["started_at is the modification time of run.json"]
    if not end_at and adapter.is_file():
        end_at, note = _utc(adapter), note + ["finished_at is the modification time of the adapter file"]
    return {"started_at": start_at, "finished_at": end_at, "wall_seconds_total": end.get("wall_seconds"),
            "source": "run files" if not note else "file times", "note": "; ".join(note) or None}


def _eval_files(spec: str, name: str, task: str) -> Path:
    p = Path(spec).expanduser()
    return p / f"{name}-{task}.jsonl" if p.is_dir() else Path(f"{p}-{task}.jsonl")


def _answers(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    rows = (json.loads(line) for line in complete_lines(path))
    return {r["id"]: str(r["output"]) for r in rows}


def _meta(path: Path, name: str, task: str, per_group: int, n: int, data_sha: str, adapter_sha: str | None) -> dict:
    """The run_model.py meta file beside an answers file, checked against this run; refuses on any mismatch."""
    meta_path = path.with_name(path.name + ".meta.json")
    if not meta_path.is_file():
        refuse(f"{path.name} has no {meta_path.name}, so its adapter is not known")
    meta = _json(meta_path)
    want = {"adapter_sha256": adapter_sha if name == "tuned" else None, "data_sha256": data_sha, "task": task,
            "per_group": per_group, "examples": n}
    for key, value in want.items():
        if meta.get(key) != value:
            refuse(f"{meta_path.name}: {key} is {meta.get(key)!r}, this run needs {value!r}")
    return meta


def _scored(examples: list[dict], outputs: dict[str, str], task: str, step: int | None = None,
            adapter_sha: str | None = None, checked: bool | None = None) -> dict:
    have = sum(ex["id"] in outputs for ex in examples)
    out = {"complete": have == len(examples), "answers": have, "of": len(examples)}
    if step is not None:
        out["checkpoint_step"] = step
    if checked is not None:
        out["checked"] = checked
        out["adapter_sha256"] = adapter_sha
    if out["complete"]:
        out["scores"] = score.score(examples, outputs)[task]
    return out


def _guard(ex: dict, output: str | None) -> str | None:
    if output is None:
        return None
    if ex["task"] == "planner":
        plan = tasks.guard_planner(output, tasks.opens_of(ex))
        return "fell back" if plan is None else f"acted: {plan.tool}"
    template, threshold = tasks.guard_question(output, ex["question"])
    return "refused" if template is None else f"acted: {template} {threshold}"


def _example(ex: dict, untuned: dict, tuned: dict) -> dict:
    u, t = untuned.get(ex["id"]), tuned.get(ex["id"])
    row = {"id": ex["id"], "task": ex["task"], "group": group(ex),
           "input": ex["question"] if ex["task"] == "question" else ex["messages"][1]["content"],
           "expected": ex["expected"], "untuned_output": u, "tuned_output": t,
           "untuned_guard": _guard(ex, u), "tuned_guard": _guard(ex, t)}
    if ex["task"] == "question":
        row["matcher"] = ex.get("matcher")
    return row


def _control(log: list[dict], path: Path) -> dict:
    """The frozen run's loss for the steps used: refuses when its ids or steps differ. Returns step -> loss and a summary."""
    rows = {r["step"]: r for r in (json.loads(x) for x in complete_lines(path))}
    for r in log:
        c = rows.get(r["step"])
        if c is None or c["id"] != r["id"]:
            refuse(f"the control log has {None if c is None else c['id']} at step {r['step']}, the run has {r['id']}")
    loss = {r["step"]: rows[r["step"]]["loss"] for r in log}
    later = [r for r in log if r["step"] > 1]
    diffs = [r["loss"] - loss[r["step"]] for r in later]
    return loss, {"steps_compared": len(log), "after_step_1": len(later),
                  "trained_below_control": sum(d < 0 for d in diffs),
                  "mean_trained_minus_control": round(math.fsum(diffs) / len(diffs), 5) if diffs else None}


def build(run_id: str, train_url: Path, data_url: Path, model: Path, model_name: str, model_revision: str,
          model_licence: str, evals: dict[str, str], question_per_group: int, planner_per_group: int, hardware: str,
          commit: str, adapter_location: str | None = None, device: str = "CPU", log: Path | None = None,
          adapter_dir: Path | None = None, stopped: str | None = None, control_log: Path | None = None) -> dict:
    train_url, data_url = Path(train_url), Path(data_url)
    run, manifest = _json(train_url / "run.json"), _json(data_url / "manifest.json")
    adapter = Path(adapter_dir or train_url / "adapter") / "adapter_model.safetensors"
    log = [json.loads(line) for line in complete_lines(Path(log or train_url / "log.jsonl"))]
    train = {ex["id"]: ex for ex in read(data_url / "train.jsonl", None)}
    unknown = [r["id"] for r in log + [{"id": i} for i in run["ids"]] if r["id"] not in train]
    if unknown:
        refuse(f"{len(unknown)} trained ids are not in train.jsonl, first {unknown[0]}")
    hashes = {name: sha256(data_url / name) for name in manifest["files"]}
    files = {name: {**rec, "matches_file": hashes[name] == rec["sha256"]} for name, rec in manifest["files"].items()}
    heldout = data_url / "heldout.jsonl"
    q, p = read(heldout, question_per_group, "question"), read(heldout, planner_per_group, "planner")
    adapter_sha = sha256(adapter) if adapter.is_file() else None
    answers, metas = {}, {}
    for name, spec in sorted(evals.items()):
        answers[name] = {}
        for task, ex, per_group in (("question", q, question_per_group), ("planner", p, planner_per_group)):
            path = _eval_files(spec, name, task)
            answers[name][task] = _answers(path)
            if path.is_file():
                if name == "tuned" and adapter_sha is None:
                    refuse(f"{path.name} is a tuned answers file but there is no adapter file to tie it to")
                metas[name, task] = _meta(path, name, task, per_group, len(ex), hashes["heldout.jsonl"], adapter_sha)
    steps_done = len(log)
    ev = {"question": {"matcher": _scored(q, score.matcher_outputs(q), "question"),
                       "perfect": _scored(q, {e["id"]: e["expected"] for e in q}, "question")},
          "planner": {"perfect": _scored(p, {e["id"]: e["expected"] for e in p}, "planner")}}
    for name, step in (("untuned", 0), ("tuned", steps_done)):
        for task, ex in (("question", q), ("planner", p)):
            meta = metas.get((name, task))
            ev[task][name] = _scored(ex, answers.get(name, {}).get(task, {}), task, step,
                                     meta and meta["adapter_sha256"], meta is not None)
    if adapter_location is None:
        home = Path.home()
        adapter_location = "~/" + str(adapter.relative_to(home)) if home in adapter.resolve().parents else adapter.parent.name
    end = _json(train_url / "run-end.json") if (train_url / "run-end.json").is_file() else {}
    control, summary = _control(log, Path(control_log)) if control_log else ({}, None)
    planned = run["examples"] * run["epochs"]
    in_steps = round(sum(r["seconds"] for r in log), 1)
    rows = []
    for r in log:
        row = [r["step"], "q" if train[r["id"]]["task"] == "question" else "p", r["tokens"], r["loss"], r["seconds"]]
        rows.append(row + [control[r["step"]]] if control else row)
    return {
        "format_version": 1, "run_id": run_id, "synthetic": True, "note": NOTE,
        "base_model": _base_model(Path(model), model_name, model_revision, model_licence),
        "adapter": {"lora": run["lora"], "trainable_parameters": run["trainable_parameters"],
                    "adapter_model.safetensors": {"bytes": adapter.stat().st_size if adapter.is_file() else None,
                                                  "sha256": adapter_sha},
                    "weights": safetensors_stats(adapter) if adapter.is_file() else {"parsed": False, "reason": "no adapter file"},
                    "b_mean_abs_from_run_end": end.get("adapter_b_mean_abs"), "location": adapter_location},
        "training": {"planned_steps": planned, "steps_done": steps_done, "stopped_by_plan": bool(stopped),
                     "stopped_reason": stopped, "complete": steps_done == planned and not stopped,
                     "control": summary},
        "data": {"seed": manifest["seed"], "split_rule": manifest["split_rule"], "files": files,
                 "counts": manifest["counts"],
                 "trained_on": _count([train[r["id"]] for r in log]),
                 "planned": _count([train[i] for i in run["ids"]]),
                 "held_out_subsample": {"rule": RULE, "question_per_group": question_per_group,
                                        "planner_per_group": planner_per_group,
                                        "question": _count(q), "planner": _count(p)}},
        "settings": {"epochs": run["epochs"], "learning_rate": run["lr"], "max_length": run["max_len"],
                     "per_group": run["per_group"], "examples": run["examples"], "seed": run["seed"],
                     "longest_example_tokens": run["longest_example_tokens"], "batch_size": 1,
                     "loss": "answer tokens only", "optimizer": run.get("optimizer"), "gradients": run.get("gradients")},
        "framework": _framework(run, device), "hardware": hardware, "commit": commit,
        "scripts_sha256": {n: sha256(HERE / n) for n in ("train_lora.py", "common.py")},
        "times": _times(train_url, run, adapter),
        "wall_seconds_in_steps": in_steps,
        "wall_time": {"seconds": end["wall_seconds"], "source": "run-end.json"} if end.get("wall_seconds") is not None
        else {"seconds": in_steps, "source": "sum of step seconds"},
        "step_columns": "step, task, tokens, loss, seconds" + (", control loss" if control else ""),
        "steps": rows,
        "evaluations": ev,
        "examples": [_example(e, answers.get("untuned", {}).get(e["task"], {}), answers.get("tuned", {}).get(e["task"], {}))
                     for e in q + p],
    }


SCORE_NOTES = [
    "The scores are on the blind held-out question subsample, not on research/agent/eval_questions.json. "
    "One run each, greedy decoding, no error bars.",
    "grounding_passed counts the answered questions whose numbers passed the code check against the tool result. "
    "That check is code and is the same before and after tuning."]


def run_experiment(template: str, args: dict) -> bool:
    """One real experiment on the tuning seeds, then the same code check `ask` uses. A tool error is not a pass.
    Config.from_env reads the real environment, so tests pass their own function."""
    try:
        result, _ = tools.call(Config.from_env(), "evaluate_rules", args)
    except tools.ToolError:
        return False
    try:
        answer, used = ask.build(template, args, result)
        return ask.grounded(answer, used, args, result)
    except Exception:  # noqa: BLE001  (any failure of the check is not a pass)
        return False


def scores_file(doc: dict, q: list[dict], experiment=run_experiment) -> dict:
    """{"scores", "notes"} for the export. Counts come from the answers after the guard."""
    ex = {r["id"]: r for r in doc["examples"]}
    if not all(doc["evaluations"]["question"][k]["complete"] for k in ("untuned", "tuned")):
        refuse("a question run is incomplete, so no scores file is written")
    adapter, base = doc["adapter"]["adapter_model.safetensors"]["sha256"], doc["base_model"]["name"]
    cache: dict[str, bool] = {}
    runs = []
    for label, key, model in (("before", "untuned_output", base), ("after", "tuned_output", f"{base} + LoRA adapter {(adapter or '')[:12]}")):
        answered = correct = passed = 0
        for e in q:
            template, threshold = tasks.guard_question(ex[e["id"]][key], e["question"])
            if template is None:
                continue
            answered += 1
            correct += e["label"] == {"template": template, "threshold": threshold}
            args = {"dataset": "ward-v2", "seeds": list(range(1, 11)), "window": "early",
                    "rules": ask._rules(template, threshold, e["question"])}
            k = json.dumps(args, sort_keys=True)
            if k not in cache:
                cache[k] = experiment(template, args)
                print(f"experiment {len(cache)}: {template} {'passed' if cache[k] else 'not passed'}", file=sys.stderr, flush=True)
            passed += cache[k]
        runs.append({"label": label, "model": model, "answered": answered, "refused": len(q) - answered,
                     "grounding_passed": passed, "correct_template": correct})
    sub = doc["data"]["held_out_subsample"]
    held = doc["data"]["files"]["heldout.jsonl"]["sha256"][:12]
    return {"scores": {"question_set": f"blind held-out questions, first {sub['question_per_group']} of each group "
                                       f"({len(q)} questions; heldout.jsonl sha256 {held})",
                       "n": len(q), "runs": runs}, "notes": SCORE_NOTES}


def dump(doc: dict) -> bytes:
    data = (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(data) > MAX_BYTES:
        refuse(f"the run log is {len(data)} bytes, over {MAX_BYTES}")
    return data


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--train_url", required=True, type=Path)
    ap.add_argument("--data_url", required=True, type=Path)
    ap.add_argument("--model", required=True, type=Path)
    ap.add_argument("--model-name", required=True)
    ap.add_argument("--model-revision", required=True)
    ap.add_argument("--model-licence", required=True)
    ap.add_argument("--eval", action="append", default=[], metavar="NAME=PATH")
    ap.add_argument("--question-per-group", type=int, required=True)
    ap.add_argument("--planner-per-group", type=int, required=True)
    ap.add_argument("--hardware", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--adapter-location")
    ap.add_argument("--device", default="CPU")
    ap.add_argument("--log", type=Path)
    ap.add_argument("--adapter", type=Path)
    ap.add_argument("--control-log", type=Path)
    ap.add_argument("--stopped")
    ap.add_argument("--scores-out", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    evals = dict(e.split("=", 1) for e in a.eval)
    if set(evals) - {"untuned", "tuned"}:
        ap.error("--eval names are untuned and tuned")
    doc = build(a.run_id, a.train_url, a.data_url, a.model, a.model_name, a.model_revision, a.model_licence, evals,
                a.question_per_group, a.planner_per_group, a.hardware, a.commit, a.adapter_location, a.device,
                a.log, a.adapter, a.stopped, a.control_log)
    data = dump(doc)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_bytes(data)
    print(f"wrote {a.out.name}: {len(data)} bytes, {len(doc['steps'])} steps, {len(doc['examples'])} examples")
    if a.scores_out:
        try:
            scores = scores_file(doc, read(a.data_url / "heldout.jsonl", a.question_per_group, "question"))
        except AgentError as e:
            refuse(str(e))
        a.scores_out.parent.mkdir(parents=True, exist_ok=True)
        a.scores_out.write_text(json.dumps(scores, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
