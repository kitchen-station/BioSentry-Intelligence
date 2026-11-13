"""The `export` command: the ledger as one file in the shape of the evaluation contract
(`biosentry-evaluation/1`, section `research-agent`). Read only. Same ledger bytes and same
arguments give the same bytes. Every refusal is one line."""

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import date
from pathlib import Path

from research import evaluate

from . import ask, ledger
from .config import REPO, AgentError, Config

MAX_FILE = 256 * 1024
MAX_QUESTIONS, MAX_PROPOSALS, MAX_NOTES, MAX_SOURCES, MAX_TEXT = 200, 50, 10, 10, 300
TEMPLATES = ("news2_threshold", "baseline_vs_news2", "alerts_per_nurse")
BANNED = ("AI predicts deterioration", "reinforcement learning", "self-learning", "conformal guarantee",
          "fewer false alarms than NEWS2")
RUN_KEYS = ("label", "model", "answered", "refused", "grounding_passed", "correct_template")


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise AgentError("usage: export --date YYYY-MM-DD --out <file.json> [--ledger <file>] [--commit <40 hex>] "
                         "[--source <text>]... [--scores <file>]")


def _int(x: object) -> bool:
    return type(x) is int and x >= 0


def check_scores(doc: object) -> tuple[dict, list[str]]:
    """The scores file: exactly {scores, notes} and exactly the keys the contract names."""
    if not isinstance(doc, dict) or set(doc) != {"scores", "notes"}:
        raise AgentError("scores file: it must be an object with exactly scores and notes")
    s, notes = doc["scores"], doc["notes"]
    if not isinstance(s, dict) or set(s) != {"question_set", "n", "runs"}:
        raise AgentError("scores file: scores needs exactly question_set, n and runs")
    if type(s["question_set"]) is not str or len(s["question_set"]) > MAX_TEXT or not _int(s["n"]):
        raise AgentError("scores file: question_set must be a string of at most 300 characters and n an integer")
    runs = s["runs"]
    if not isinstance(runs, list) or len(runs) != 2:
        raise AgentError("scores file: runs must hold exactly two runs")
    for run, label in zip(runs, ("before", "after")):
        if not isinstance(run, dict) or set(run) != set(RUN_KEYS) or run["label"] != label \
                or type(run["model"]) is not str or not all(_int(run[k]) for k in RUN_KEYS[2:]):
            raise AgentError(f"scores file: the {label} run is malformed")
        if run["answered"] + run["refused"] != s["n"] or run["grounding_passed"] > run["answered"] \
                or run["correct_template"] > run["answered"]:
            raise AgentError(f"scores file: the {label} counts do not add up")
    if not isinstance(notes, list) or not all(type(n) is str and len(n) <= MAX_TEXT for n in notes):
        raise AgentError("scores file: notes must be a list of strings of at most 300 characters")
    return s, notes


def _questions(records: list[dict]) -> tuple[list[dict], int]:
    steps = {r["id"]: r for r in records if r["type"] == "step"}
    out, left = [], 0
    for start in (r for r in records if r["type"] == "start" and r["kind"] == "ask"):
        step = steps.get(start["id"])
        calls = step["calls"] if step else None
        if step is None or (calls and calls[0].get("ok") is False):
            left += 1
            continue
        plan = start["plan"]
        q = {"id": start["id"], "asked_at": start["at"], "question": start.get("question", ""), "template": "none",
             "status": "refused", "answer": ask.REFUSAL.splitlines(), "numbers": []}
        if calls:
            q["template"] = plan.get("rule") if isinstance(plan, dict) else None
            if q["template"] not in TEMPLATES:
                raise AgentError(f"{start['id']}: the template is not one of the three")
            if step.get("grounding") == "ok":
                answer, used = _rebuild(start["id"], q["template"], calls[0], step["conclusion"])
                q.update(status="answered", answer=answer.splitlines(), numbers=used)
            elif step.get("grounding") != "failed":
                raise AgentError(f"{start['id']}: the step has no grounding mark")
        out.append(q)
    return out, left


def _rebuild(sid: str, template: str, call: dict, stored: str) -> tuple[str, list[str]]:
    """The answer from the stored call. It must be grounded and equal to what the ledger kept."""
    try:
        args, result = call["args"], call["result"]
        answer, used = ask.build(template, args, result)
        ok = ask.grounded(answer, used, args, result) and stored == ledger.clean(" ".join(answer.splitlines()), 2000)
    except (KeyError, TypeError, IndexError, AttributeError):
        ok = False
    if not ok:
        raise AgentError(f"{sid}: the stored answer does not match the stored tool result")
    return answer, used


def _proposals(records: list[dict]) -> list[dict]:
    steps = {r["id"]: r for r in records if r["type"] == "step"}
    out = []
    for r in (r for r in records if r["type"] == "proposal"):
        if r["step"] not in steps:
            raise AgentError(f"{r['id']}: its step is not in the ledger")
        approval = next((x for x in records if x["type"] == "approval" and x["proposal"] == r["id"]
                         and x["seq"] > r["seq"]), None)
        out.append({"id": r["id"], "action": r["action"], "step": r["step"],
                    "conclusion": steps[r["step"]]["conclusion"], "state": ledger.status(records, r["id"]),
                    "approved_by": approval["by"] if approval else None,
                    "approved_at": approval["at"] if approval else None})
    return out


def _strings(obj: object):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def _no_floats(obj: object) -> None:
    if isinstance(obj, float):
        raise AgentError("a float is in the export; refused")
    for v in (obj.values() if isinstance(obj, dict) else obj if isinstance(obj, list) else ()):
        _no_floats(v)


def _ban(doc: dict) -> None:
    body = doc["body"]
    parts = [(q["id"], q) for q in body["questions"]] + [(p["id"], p) for p in body["proposals"]]
    parts.append(("the envelope", {**doc, "body": {**body, "questions": [], "proposals": []}}))
    for rid, part in parts:
        for text in _strings(part):
            for phrase in BANNED:
                if phrase.lower() in text.lower():
                    raise AgentError(f"banned phrase {phrase!r} in {rid}")


def build(records: list[dict], data: bytes, date_: str, commit: str, source: list[str], scores: tuple | None) -> dict:
    questions, left = _questions(records)
    proposals = _proposals(records)
    notes = []
    if left:
        notes.append(f"{left} questions are left out: the tool failed or the step did not finish.")
    if len(questions) > MAX_QUESTIONS:
        notes.append(f"Only the last {MAX_QUESTIONS} of {len(questions)} questions are shown.")
        questions = questions[-MAX_QUESTIONS:]
    if len(proposals) > MAX_PROPOSALS:
        notes.append(f"Only the last {MAX_PROPOSALS} of {len(proposals)} proposals are shown.")
        proposals = proposals[-MAX_PROPOSALS:]
    if scores:
        notes += scores[1]
    if len(notes) > MAX_NOTES:
        raise AgentError(f"more than {MAX_NOTES} notes")
    doc = {"format": "biosentry-evaluation/1", "section": "research-agent",
           "provenance": {"repo": "kitchen-station/BioSentry-Intelligence", "commit": commit, "source": source,
                          "exporter": {"name": "research.agent export", "version": "1"}, "date": date_,
                          "synthetic": True, "validated": False, "data": "ward-v2 simulator trials",
                          "seeds": [{"role": "tuning", "seeds": sorted(evaluate.TUNING)}]},
           "body": {"ledger": {"records": len(records), "sha256": hashlib.sha256(data).hexdigest()},
                    "questions": questions, "proposals": proposals, "scores": scores[0] if scores else None},
           "notes": notes}
    _ban(doc)
    _no_floats(doc)
    return doc


def _git_head() -> str:
    try:
        p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        p = None
    out = p.stdout.strip() if p and p.returncode == 0 else ""
    if not re.fullmatch(r"[0-9a-f]{40}", out):
        raise AgentError("cannot read the commit from git; give --commit")
    return out


def run(cfg: Config, args: list[str]) -> str:
    ap = _Parser(add_help=False, allow_abbrev=False)
    ap.add_argument("--date", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ledger")
    ap.add_argument("--commit")
    ap.add_argument("--source", action="append", default=[])
    ap.add_argument("--scores")
    a = ap.parse_args(args)
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", a.date):
            raise ValueError
        date.fromisoformat(a.date)
    except ValueError:
        raise AgentError("--date must be a real date, YYYY-MM-DD") from None
    if a.commit is not None and not re.fullmatch(r"[0-9a-f]{40}", a.commit):
        raise AgentError("--commit must be 40 lower-case hex characters")
    if len(a.source) > MAX_SOURCES:
        raise AgentError(f"at most {MAX_SOURCES} --source")
    out = ledger.export_path(cfg, a.out)
    scores = None
    if a.scores:
        try:
            scores = check_scores(json.loads(Path(a.scores).read_text()))
        except (OSError, ValueError, RecursionError):
            raise AgentError("scores file: cannot read it as JSON") from None
    if a.ledger:
        if not Path(a.ledger).is_file():
            raise AgentError("--ledger: no such file")
        records, data = ledger.read_path(Path(a.ledger))
    else:
        records, data = ledger.read_path(cfg.home / ledger.LEDGER)
    doc = build(records, data, a.date, a.commit or _git_head(), [ledger.clean(s, MAX_TEXT) for s in a.source], scores)
    text = json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if len(text.encode()) > MAX_FILE:
        raise AgentError("the export is larger than 256 KB")
    try:
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, out)
    except OSError:
        raise AgentError("cannot write the export file") from None
    return f"exported {len(doc['body']['questions'])} questions, {len(doc['body']['proposals'])} proposals"
