"""The manifest of shipped model files (slice M4) and the registry table made from it. Standard library only.

    python -m research.manifest --write      # models/manifest.json, from the files and their own metadata
    python -m research.manifest --check      # exit 2, one line per difference (the manifest only)
    python -m research.manifest --registry   # the models table in training/REGISTRY.md (between its markers)
    (all take --root <repository folder>; the default is this repository)

Nothing is typed by hand and nothing is guessed: a field that is recorded nowhere is null. The
`commit_at_run` cannot come from git (research code starts no process); it is the `commit` of the run log
that made the file (the branch commit when the run was made; it may not exist after a squash merge), or null with a `commit_note`. The fixed rules are in `training/README.md`.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
STUDENT = "tier_c/student_vitals"
REWARD = "tier_b/reward_policy"
CONFIDENCE = "tier_c/confidence_check"
BASELINE = "tier_b/baseline"

# The rule for "what is a shipped model file", by folder and file name under models/ (nothing else counts):
STUDENT_FILES = {  # file name -> (variant, format)
    "model.onnx": ("all", "ONNX"), "model.mindir": ("all", "MindIR"), "model.ms": ("all", "MindSpore Lite"),
    "model.json": ("all", None), "model_no_temp.onnx": ("no_temp", "ONNX"),
    "model_no_temp.mindir": ("no_temp", "MindIR"), "model_no_temp.ms": ("no_temp", "MindSpore Lite"),
    "model_no_temp.json": ("no_temp", None)}  # None: the format name is read from the file itself
MODEL_FILES = {  # path under models/ -> (model id, variant, format)
    f"{REWARD}/policy.json": (REWARD, "all", "JSON"),
    f"{REWARD}/no_temperature/policy.json": (REWARD, "no_temp", "JSON"),
    f"{CONFIDENCE}/calibration.json": (CONFIDENCE, "all", "JSON"),
    f"{CONFIDENCE}/calibration_no_temp.json": (CONFIDENCE, "no_temp", "JSON"),
    f"{BASELINE}/params.json": (BASELINE, "all", "JSON"),
    **{f"{STUDENT}/{n}": (STUDENT, v, f) for n, (v, f) in STUDENT_FILES.items()}}
WRITTEN_BY = {".mindir": "MindSpore export", ".onnx": "onnx.helper, built from the MindSpore weights",
              ".ms": "MindSpore Lite converter", ".json": "research.export_plain"}
# Everything else under models/ must be a record: this rule. Anything that is neither is refused, so a
# new model file cannot slip in without a line in MODEL_FILES.
RECORD_SUFFIXES = {".md", ".txt", ".jsonl", ".sh", ".sha256"}
RECORD_NAMES = {"metadata.json", "exports.json", "timing.json", "manifest.json",
                "held_out.json", "heldout.json", "held_out_2.json", "heldout_2.json",
                "analysis.json"}  # the one-time held-out results (set 1 and set 2) and the set-2 analysis
RECORD_FOLDERS = {"rounds", "generations"}  # generation records and the dropped rounds' policies


def is_record(rel: Path) -> bool:
    return rel.suffix in RECORD_SUFFIXES or rel.name in RECORD_NAMES or bool(RECORD_FOLDERS & set(rel.parts))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(path: Path):
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError, RecursionError):
        return None


def ranges(seeds) -> str | None:
    """[61, 62, 63, 70] -> '61-63,70'."""
    if not isinstance(seeds, list) or not seeds or not all(type(s) is int for s in seeds):
        return None
    out, a, b = [], seeds[0], seeds[0]
    for s in [*seeds[1:], None]:
        if s is not None and s == b + 1:
            b = s
            continue
        out.append(str(a) if a == b else f"{a}-{b}")
        a = b = s
    return ",".join(out)


def _logs(root: Path, model: str) -> list[dict]:
    """The run logs of a model, oldest to newest by file name (the names carry the date)."""
    folder = root / "training" / "runs" / Path(model).name
    found = []
    for p in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        obj = _json(p)
        if isinstance(obj, dict) and obj.get("model") == model:
            found.append(obj)
    return found


def _ids(log: dict | None, none_why: str) -> dict:
    run_id, commit = (None, None) if log is None else (log.get("run_id"), log.get("commit"))
    out = {"run_id": run_id, "commit_at_run": commit}
    if commit is None:
        out["commit_note"] = none_why if log is None else "the run log records no commit"
    return out


def _student_log(root: Path, meta: dict | None) -> dict | None:
    """The newest log of the same framework version, seeds and student sizes as metadata.json.
    shortcut: it does not match on the weights (the log keeps none); two runs that agree on all of
    these would be told apart only by file name. Upgrade if a log ever records the ONNX sha256."""
    if not isinstance(meta, dict):
        return None
    seeds, variants = meta.get("seeds_read") or {}, meta.get("variants") or {}
    for log in reversed(_logs(root, STUDENT)):
        data = log.get("data") or {}
        sizes = lambda v: {t: ((x or {}).get("parameters") or {}).get("student") for t, x in v.items()}  # noqa: E731
        if (log.get("framework_version") == meta.get("framework_version") and data.get("train_seeds") == seeds.get("train")
                and data.get("validation_seeds") == seeds.get("validation") and sizes(log.get("variants") or {}) == sizes(variants)):
            return log
    return None


def _record(root: Path, rel: str, data: bytes) -> dict:
    model, variant, fmt = MODEL_FILES[rel]
    base = {"path": f"models/{rel}", "model": model, "variant": variant, "bytes": len(data), "sha256": sha256(data)}
    if model == STUDENT:
        meta = _json(root / "models" / STUDENT / "metadata.json")
        meta = meta if isinstance(meta, dict) else {}
        if fmt is None:
            obj = _json(root / "models" / rel)
            fmt = obj.get("format") if isinstance(obj, dict) and isinstance(obj.get("format"), str) else None
        seeds = meta.get("seeds_read") or {}
        return base | {"format": fmt, "written_by": WRITTEN_BY[Path(rel).suffix], "framework": meta.get("framework"), "framework_version": meta.get("framework_version"),
                       "training_seeds": {"train": ranges(seeds.get("train")), "validation": ranges(seeds.get("validation"))},
                       **_ids(_student_log(root, meta), "no run log under training/runs/student_vitals/ matches metadata.json")}
    obj = _json(root / "models" / rel)
    obj = obj if isinstance(obj, dict) else {}
    if model == REWARD:  # the run log whose ledger names this file's sha256 as shipped
        hit = None
        for log in reversed(_logs(root, REWARD)):
            if any(((r.get("ledger") or {}).get("shipped") or {}).get("policy_sha256") == base["sha256"]
                   for r in log.get("runs") or []):
                hit = log
                break
        seeds = obj.get("seeds") or {}
        gen = f"{seeds['first']}-{seeds['last']}" if {"first", "last"} <= set(seeds) else None
        return base | {"format": fmt, "framework": "none (six numbers, plain arithmetic)", "framework_version": None,
                       "training_seeds": {"generations": gen, "validation": ranges(obj.get("validation_seeds"))},
                       **_ids(hit, "no run log under training/runs/reward_policy/ names this file")}
    if model == CONFIDENCE:
        hit = None
        for log in reversed(_logs(root, CONFIDENCE)):
            if any((v or {}).get("calibration_sha256") == base["sha256"] for v in (log.get("variants") or {}).values()):
                hit = log
                break
        return base | {"format": fmt, "framework": "none (one quantile of drawn scores, plain arithmetic)",
                       "framework_version": None, "training_seeds": {"calibration": ranges(obj.get("seeds"))},
                       **_ids(hit, "no run log under training/runs/confidence_check/ names this file")}
    return base | {"format": fmt, "framework": "none (median and MAD arithmetic)", "framework_version": None,
                   "training_seeds": {"chosen_on": obj.get("chosen_on")},
                   **_ids(None, "chosen by research.sweep, which writes no run log")}


def scan(root: Path) -> tuple[list[dict], list[str]]:
    """(the records of every model file found, the files that are neither a model file nor a record)."""
    models_dir = root / "models"
    files = sorted(p.relative_to(models_dir) for p in models_dir.rglob("*") if p.is_file()) if models_dir.is_dir() else []
    records, bad = [], []
    for rel in files:
        key = rel.as_posix()
        if key in MODEL_FILES:
            records.append(_record(root, key, (models_dir / rel).read_bytes()))
        elif not is_record(rel):
            bad.append(f"models/{key}")
    return sorted(records, key=lambda r: r["path"]), bad


def build(root: Path) -> bytes:
    records, bad = scan(root)
    if bad:
        raise Refused("; ".join(f"{b}: neither a listed model file nor a record (add it to MODEL_FILES or the record rule)" for b in bad))
    top = {"schema": 1, "generated_by": "python -m research.manifest --write",
           "note": "never edited by hand; every field comes from the file or its metadata, null when not recorded",
           "models": records}
    return (json.dumps(top, indent=1, sort_keys=True) + "\n").encode()


class Refused(Exception):
    pass


def differences(root: Path) -> list[str]:
    """One line per difference between models/manifest.json and what the files say now."""
    records, bad = scan(root)
    lines = [f"{b}: neither a listed model file nor a record" for b in bad]
    try:
        listed = json.loads((root / "models" / "manifest.json").read_bytes())["models"]
        listed = {r["path"]: r for r in listed}
    except (OSError, ValueError, KeyError, TypeError):
        return [*lines, "models/manifest.json: missing or not a manifest (run python -m research.manifest --write)"]
    now = {r["path"]: r for r in records}
    for path in sorted(set(listed) | set(now)):
        if path not in now:
            lines.append(f"{path}: listed in the manifest but the file is missing")
        elif path not in listed:
            lines.append(f"{path}: model file is not listed in the manifest")
        else:
            for k in sorted(set(listed[path]) | set(now[path])):
                if listed[path].get(k) != now[path].get(k):
                    lines.append(f"{path}: {k} differs (manifest {listed[path].get(k)!r}, now {now[path].get(k)!r})")
    if not lines and (root / "models" / "manifest.json").read_bytes() != build(root):
        lines.append("models/manifest.json: bytes are not the ones --write makes (key order or layout)")
    return lines


# --- the registry -----------------------------------------------------------------------------------

BEGIN = "<!-- models-table: generated by python -m research.manifest --registry; do not edit between the markers -->"
END = "<!-- /models-table -->"
COLUMNS = ("model", "variant", "framework", "format", "written by", "bytes", "path", "sha256", "training seeds", "run id", "commit at run")


def _cell(v) -> str:
    return "-" if v is None else str(v).replace("|", "/")


def table(root: Path) -> str:
    try:
        models = json.loads((root / "models" / "manifest.json").read_bytes())["models"]
    except (OSError, ValueError, KeyError, TypeError):
        raise Refused("models/manifest.json: missing or not a manifest (run --write first)") from None
    rows = [f"| {' | '.join(COLUMNS)} |", f"|{'---|' * len(COLUMNS)}"]
    for m in models:
        fw = " ".join(x for x in (m["framework"], m["framework_version"]) if x)
        seeds = "; ".join(f"{k} {v}" for k, v in m["training_seeds"].items() if v)
        rows.append("| " + " | ".join(_cell(x) for x in (m["model"], m["variant"], fw or None, m["format"], m.get("written_by"), m["bytes"],
                                                          f"`{m['path']}`", f"`{m['sha256']}`", seeds or None,
                                                          m["run_id"], m["commit_at_run"])) + " |")
    return "\n".join(rows)


def registry_is_current(root: Path) -> bool:
    text = (root / "training" / "REGISTRY.md").read_text()
    return registry_text(root, text) == text


def registry_text(root: Path, current: str) -> str:
    """`current` with only the part between the markers replaced."""
    if current.count(BEGIN) != 1 or current.count(END) != 1 or current.index(BEGIN) > current.index(END):
        raise Refused("training/REGISTRY.md: the models-table markers are missing or repeated")
    head, rest = current.split(BEGIN)
    _, tail = rest.split(END)
    return f"{head}{BEGIN}\n\n{table(root)}\n\n{END}{tail}"


# --- the command ------------------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="research.manifest", add_help=False, allow_abbrev=False)
    for flag in ("--write", "--check", "--registry"):
        ap.add_argument(flag, action="store_true")
    ap.add_argument("--root", default=str(ROOT))
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        print("usage: python -m research.manifest --write | --check | --registry [--root <folder>]", file=sys.stderr)
        return 2
    root = Path(a.root)
    if [a.write, a.check, a.registry].count(True) != 1:
        print("usage: python -m research.manifest --write | --check | --registry [--root <folder>]", file=sys.stderr)
        return 2
    try:
        if a.write:
            data = build(root)
            (root / "models" / "manifest.json").write_bytes(data)
        elif a.registry:
            path = root / "training" / "REGISTRY.md"
            path.write_text(registry_text(root, path.read_text()))
        else:
            lines = differences(root)
            for line in lines:
                print(line, file=sys.stderr)
            return 2 if lines else 0
    except (Refused, OSError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
