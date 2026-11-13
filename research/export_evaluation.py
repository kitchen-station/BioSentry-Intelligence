"""The two exported files of the evaluation data contract (v3.4) that this repository owns, each written by one
command (standard library only).

    python -m research.export_evaluation registry    --date YYYY-MM-DD --commit <40 hex> [--source <text>]... --out <path>
    python -m research.export_evaluation evaluations --date YYYY-MM-DD --commit <40 hex> [--source <text>]...
                                         --held-out <held-out.json>... [--drop-rule <id>]... [--confidence <heldout.json>] --out <path>

The held-out JSON files are what `--json` of research.evaluate, research.evaluate_student,
research.reward_loop --evaluate and research.confidence evaluate write. A text report is never a source here.
The same inputs and the same --date give the same bytes. A file with a banned word, a cap broken or a number
that has no plain spelling is refused and nothing is written. Exit 2 with one line on any refusal.
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
FORMATS = {"registry": "biosentry-evaluation/2", "evaluations": "biosentry-evaluation/4"}  # contract v3.6
HELD_FORMAT = "biosentry-held-out/1"  # research/evaluate.py HELD_OUT_FORMAT
EXPORTER = {"name": "research.export_evaluation", "version": "1"}
REPO = "kitchen-station/BioSentry-Intelligence"
MAX_BYTES = 256 * 1024
BANNED = ("ai predicts deterioration", "reinforcement learning", "self-learning", "conformal guarantee",
          "fewer false alarms than news2")
VALIDATED = re.compile(r"(?<!not )(?<!un)\bvalidated\b")  # only a positive claim is banned
RULE_CAP = 30  # contract v3.4


class Refused(Exception):
    pass


# --- number kinds -------------------------------------------------------------------------------

def _num(x, digits: int | None):
    if x is None:
        return None
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise Refused(f"not a finite number: {x!r}")
    if digits is None:
        if x != int(x):
            raise Refused(f"not a whole number: {x!r}")
        return int(x)
    return float(f"{x:.{digits}f}") + 0.0  # + 0.0 turns -0.0 into 0.0


def INT(x):
    return _num(x, None)


def D1(x):
    return _num(x, 1)


def D3(x):
    return _num(x, 3)


def check_tree(obj, path: str = "") -> None:
    """No exponent notation, no NaN, no banned word, anywhere."""
    if isinstance(obj, float):
        if not math.isfinite(obj) or "e" in repr(obj) or "E" in repr(obj):
            raise Refused(f"{path}: {obj!r} has no plain spelling")
    elif isinstance(obj, str):
        low = obj.lower()
        for w in BANNED:
            if w in low:
                raise Refused(f"{path}: banned words: {w!r}")
        if VALIDATED.search(low):
            raise Refused(f"{path}: 'validated' as a claim")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            check_tree(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            check_tree(v, f"{path}[{i}]")


def cap(items: list, n: int, what: str) -> list:
    if len(items) > n:
        raise Refused(f"{what}: {len(items)} entries, the cap is {n}")
    return items


def short(s: str, n: int, what: str) -> str:
    if len(s) > n:
        raise Refused(f"{what}: {len(s)} characters, the cap is {n}")
    return s


# --- reading files ------------------------------------------------------------------------------

def read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        raise Refused(f"{path.name}: cannot be read") from None


def load_json(path: Path):
    try:
        return json.loads(read(path))
    except (ValueError, RecursionError):
        raise Refused(f"{path.name}: not valid JSON") from None


# --- the held-out JSON into the contract's rules and context ---------------------------------------

_BASE = {"news2_chart_trigger": "NEWS2 chart trigger", "news2_total_ge_5": "NEWS2 total of 5 or more",
         "baseline_alone": "own baseline", "baseline": "own baseline", "teacher": "teacher network", "student": "student",
         "student_checked": "checked student", "reward_gen0": "reward policy, generation 0",
         "reward_final": "reward policy, final"}


def rule_name(rid: str) -> tuple[str, str | None]:
    base, temp = (rid[: -len("_no_temp")], True) if rid.endswith("_no_temp") else (rid, False)
    adds = base.startswith("news2_or_")
    core = base[len("news2_or_"):] if adds else base
    name = _BASE.get(core, core.replace("_", " "))
    name = ("NEWS2 or " + name) if adds else name[:1].upper() + name[1:]
    return name + (" (no temperature)" if temp else ""), ("news2" if adds else None)


def _fr(f: dict, ci: bool = False) -> dict:
    out = {"n": INT(f["n"]), "d": INT(f["d"]), "rate": D3(f["rate"])}
    if ci:
        out |= {"lo": D3(f["lo"]), "hi": D3(f["hi"])}
    return out


def export_rule(r: dict) -> dict:
    name, adds = rule_name(r["id"])
    lead = r["lead"]
    return {"id": r["id"], "name": name, "adds_to": adds,
            "per_shift": {"value": D3(r["per_shift"]["value"]), "lo": D3(r["per_shift"]["lo"]),
                          "hi": D3(r["per_shift"]["hi"])},
            "time_in_alert": _fr(r["time_in_alert"]), "new_alert": _fr(r["new_alert"], True),
            "any_bin": _fr(r["any_bin"], True), "any_bin_not_cut_short": _fr(r["any_bin_not_cut_short"]),
            "chance_new": _fr(r["chance_new"]), "chance_any": _fr(r["chance_any"]),
            "excess_any_not_cut_short": D3(r["excess_any_not_cut_short"]),
            "lead": {"n": INT(lead["n"]), "median_minutes": D1(lead["median"]), "q1_minutes": D1(lead["q1"]),
                     "q3_minutes": D1(lead["q3"])}}


def export_context(c: dict) -> dict:
    return {"shift_hours": INT(c["shift_hours"]), "bin_minutes": INT(c["bin_minutes"]),
            "ci": {"level_percent": INT(c["ci"]["level_percent"]), "resamples": INT(c["ci"]["resamples"]),
                   "generator_seed": INT(c["ci"]["generator_seed"]), "method": c["ci"]["method"]},
            "trials": INT(c["trials"]), "patients": INT(c["patients"]), "patient_days": D1(c["patient_days"]),
            "episodes_evaluated": INT(c["episodes_evaluated"]),
            "episodes_not_cut_short": INT(c["episodes_not_cut_short"]),
            "control_windows": INT(c["control_windows"]), "tuning": c["tuning"]}


def merge_held_out(files: list[dict], drop: list[str]) -> tuple[dict, list[dict], list[int], list[str]]:
    """(context, rules, held-out seeds, notes). The contexts and seeds must agree. A rule in two files must
    be the same at export precision (it is then kept once); a different one refuses."""
    ctx = seeds = None
    rules: dict[str, dict] = {}
    dup = 0
    for f in files:
        if f.get("format") != HELD_FORMAT:
            raise Refused(f"held-out file: format is not {HELD_FORMAT}")
        c, s = export_context(f["context"]), f["context"]["held_out_seeds"]
        if (ctx, seeds) not in ((None, None), (c, s)):
            raise Refused("held-out files disagree on the context or the seeds")
        ctx, seeds = c, s
        for r in f["rules"]:
            e = export_rule(r)
            if e["id"] in rules:
                if rules[e["id"]] != e:
                    raise Refused(f"rule {e['id']} differs between held-out files")
                dup += 1
            else:
                rules[e["id"]] = e
    for d in drop:
        if d not in rules:
            raise Refused(f"--drop-rule {d}: no such rule")
        del rules[d]
    out = list(rules.values())
    if len(out) > RULE_CAP:
        raise Refused(f"{len(out)} rules, the cap is {RULE_CAP}: name some with --drop-rule")
    notes = []
    if drop:
        notes.append("Rule rows dropped on request: " + ", ".join(drop) + ".")
    if not any("reward" in r["id"] for r in out):
        notes.append("The reward policy's rules are absent: its held-out result exists only as a text report, "
                     "and a text report is never a source of a number.")
    if dup:
        notes.append(f"{dup} rule rows appeared in more than one held-out file with the same values; each is kept once.")
    return ctx, out, seeds, notes


# --- the registry file ----------------------------------------------------------------------------

def build_registry(root: Path) -> tuple[dict, list[str]]:
    datasets = []
    for card in sorted((root / "models" / "data").glob("*/card.md")):
        text = card.read_text("utf-8")
        title = re.sub(r"^# (Dataset card: )?| \(data card\)$", "", text.split("\n", 1)[0])
        lic = re.search(r"Licence: (.*?)\.(?:\s|$)", re.sub(r"\s+", " ", text))
        datasets.append({"id": card.parent.name, "name": title, "path": None,
                         "licence": lic[1] if lic else "not stated",
                         "source": f"models/data/{card.parent.name}/card.md"})
    agent = read(root / "models" / "agent" / "card.md").decode("utf-8")
    w = re.search(r"^\| model that was tuned \| `([^`]+)`, ([^,|]+),", agent, re.M)
    if not w:
        raise Refused("models/agent/card.md: no 'model that was tuned' row")
    weights = [{"id": w[1], "name": w[1].split("/")[-1], "path": None, "licence": w[2].strip(),
                "source": "models/agent/card.md"}]
    notes = ["Licence is 'not stated' where the card says nothing. Open weights: only the model the agent card says "
             "was tuned; models it names as a fallback or a hosted target are not used and not listed."]
    return {"datasets": cap(datasets, 30, "datasets"), "open_weights": cap(weights, 30, "open_weights")}, notes


# --- the evaluations file ---------------------------------------------------------------------------

def _d4(x):
    """Every float at most 4 decimals; ints, text and booleans as they are."""
    if isinstance(x, dict):
        return {k: _d4(v) for k, v in x.items()}
    if isinstance(x, float):
        return _num(x, 4)
    return x


def confidence_block(path: str, root: Path) -> dict:
    """A rounded copy of the held-out file's `confidence`, with the repository path and sha256 of the bytes read."""
    f = Path(path)
    try:
        rel = (f if f.is_absolute() else Path.cwd() / f).resolve().relative_to(root.resolve())
    except ValueError:
        raise Refused("--confidence: the file must be inside the repository") from None
    raw = read(f)
    try:
        obj = json.loads(raw)
    except (ValueError, RecursionError):
        raise Refused(f"{f.name}: not valid JSON") from None
    if not isinstance(obj, dict) or obj.get("format") != HELD_FORMAT or not isinstance(obj.get("confidence"), dict):
        raise Refused(f"{f.name}: not a held-out file with a confidence block")
    return {"source": {"path": rel.as_posix(), "sha256": hashlib.sha256(raw).hexdigest()},
            "variants": {k: _d4(v) for k, v in obj["confidence"].items() if k in ("all", "no_temp")}}


def build_evaluations(held: list[dict], drop: list[str], confidence: dict | None = None) -> tuple[dict, list[str], list[dict]]:
    ctx, rules, seeds, notes = (None, None, [], []) if not held else merge_held_out(held, drop)
    body = {"context": ctx, "rules": rules, "confidence": confidence}
    if confidence is None:
        notes.append("confidence is null: no --confidence file was given.")
    return body, notes, ([{"role": "held out", "seeds": seeds}] if seeds else [])


# --- the envelope and the command -----------------------------------------------------------------------

def envelope(section: str, body: dict, notes: list[str], a, seeds: list[dict], data: str) -> bytes:
    cap(notes, 10, "notes")
    for n in notes:
        short(n, 300, "note")
    cap(seeds, 5, "provenance seeds")
    for s in seeds:
        cap(s["seeds"], 100, "provenance seeds")
    obj = {"format": FORMATS[section], "section": section, "body": body, "notes": notes,
           "provenance": {"repo": REPO, "commit": a.commit, "source": cap(list(a.source), 10, "--source"),
                          "exporter": EXPORTER, "date": a.date, "synthetic": True, "validated": False,
                          "data": data, "seeds": seeds}}
    check_tree(obj)
    out = (json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode()
    if len(out) > MAX_BYTES:
        raise Refused(f"{len(out)} bytes, the cap is {MAX_BYTES}")
    return out


def write(path: str, data: bytes) -> None:
    try:
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
    except OSError:
        raise Refused("--out: cannot write that file") from None


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise Refused(message)


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="research.export_evaluation", add_help=False, allow_abbrev=False)
    sub = p.add_subparsers(dest="command", required=True, parser_class=_Parser)
    for name in ("registry", "evaluations"):
        s = sub.add_parser(name, add_help=False, allow_abbrev=False)
        s.add_argument("--date", required=True)
        s.add_argument("--commit", required=True)
        s.add_argument("--source", action="append", default=[])
        s.add_argument("--out", required=True)
        s.add_argument("--root", default=str(ROOT))
        if name == "evaluations":
            s.add_argument("--held-out", action="append", required=True)
            s.add_argument("--drop-rule", action="append", default=[])
            s.add_argument("--confidence")
    return p


def run(argv: list[str]) -> None:
    a = _parser().parse_args(argv)
    if not re.fullmatch(r"\d{4}-\d\d-\d\d", a.date):
        raise Refused("--date must be YYYY-MM-DD")
    if not re.fullmatch(r"[0-9a-f]{40}", a.commit):
        raise Refused("--commit must be 40 lowercase hex characters")
    root = Path(a.root)
    if a.command == "registry":
        body, notes = build_registry(root)
        data = envelope("registry", body, notes, a, [], "model and dataset cards")
    else:
        held = []
        for f in a.held_out:
            obj = load_json(Path(f))
            if not isinstance(obj, dict) or "context" not in obj or "rules" not in obj:
                raise Refused(f"{Path(f).name}: not a held-out file")
            held.append(obj)
        body, notes, seeds = build_evaluations(held, a.drop_rule, confidence_block(a.confidence, root) if a.confidence else None)
        data = envelope("evaluations", body, notes, a, seeds, "ward-v2 simulator trials")
    write(a.out, data)


def main(argv: list[str]) -> int:
    try:
        run(argv)
    except (Refused, KeyError, TypeError, IndexError, ValueError) as e:
        print(f"refused: {e if isinstance(e, Refused) else 'unexpected shape (' + type(e).__name__ + ' ' + str(e)[:60] + ')'}",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
