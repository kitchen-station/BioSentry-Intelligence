"""Train the distilled vitals student (slice M1): two MindSpore networks, exports, metadata.

    python -m research.train_student <trial folder>... [--out <dir>] [--cache <dir>] [--workers N]
                                     [--lite_rows <dir>] [--byte_reproducible true|false]
                                     [--run_log <file>] [--commit <sha>] [--run_id <text>]
    python -m research.train_student --data_url <dir of trial folders> --train_url <dir> [...]

`--data_url` and `--train_url` are the names Huawei ModelArts gives a custom training job: every
sub-folder of data_url is a trial, train_url is where the files go (the same as `--out`). The command
makes no network call. Seeds 61-160 train, 261-300 validate; 301-340 and ward-v2 folders are skipped
after reading only manifest.json; any other seed is refused. Writes model.onnx, model_no_temp.onnx,
model.mindir, model_no_temp.mindir, metadata.json, exports.json and timing.json. With --lite_rows it
also writes the input and calibration files the MindSpore Lite benchmark needs (the conversion itself is
the hand-run models/tier_c/student_vitals/convert_lite.sh: research code starts no external program,
no subprocess and no shell; it does use Python's own worker processes for feature extraction).
With --run_log it also writes one small JSON run log (every fit, every epoch's training and
validation loss, the gate, thresholds, budget cuts; all wall-clock numbers under one `time` key; no
path). --commit and --run_id are recorded as given (research code cannot call git).
Exit 2 with one line on a refusal.
"""

import argparse
import json
import os
import platform
import re
import sys
import timeit
from importlib import metadata as importlib_metadata
from pathlib import Path

import mindspore as ms
import numpy as np
import onnxruntime as ort

from . import student
from .student import Built, Grid, build, load_for_training, sha256
from .trials import TrialError

OUT = Path(__file__).parent.parent / "models" / "tier_c" / "student_vitals"
FILES = {"all": "model.onnx", "no_temp": "model_no_temp.onnx"}
MINDIR = {"all": "model.mindir", "no_temp": "model_no_temp.mindir"}
GRID = Grid()  # the pre-registered values; tests replace it with small ones
PACKAGES = ("mindspore", "numpy", "scikit-learn", "scipy", "onnx", "onnxruntime", "threadpoolctl", "protobuf",
            "pydantic")
LITE_ROWS = 20
WARMUP, TIMED = 1000, 10_000


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise TrialError(message)


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="research.train_student", add_help=False, allow_abbrev=False)
    p.add_argument("folders", nargs="*")
    p.add_argument("--out")
    p.add_argument("--train_url")
    p.add_argument("--data_url")
    p.add_argument("--cache")
    p.add_argument("--workers", type=int)
    p.add_argument("--lite_rows")
    p.add_argument("--run_log")
    p.add_argument("--commit")
    p.add_argument("--run_id")
    p.add_argument("--byte_reproducible", choices=("true", "false"), default="true")
    return p


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _json_bytes(obj) -> bytes:
    return (json.dumps(obj, indent=1, sort_keys=True) + "\n").encode()


# --- inputs for the hand-run MindSpore Lite check ----------------------------------------------

def write_lite_rows(folder: Path, built: Built) -> None:
    """Per exported variant, in <folder>/<model stem>/: row<i>.bin (the float32 input [1, n]) and
    row<i>.txt (the benchmark's calibration file: a placeholder for the output tensor's name, which
    convert_lite.sh fills in from the converted model, then MindSpore's p). Only writes files."""
    for tag, r in built.results.items():
        if r.onnx is None:
            continue
        out = folder / Path(MINDIR[tag]).stem
        out.mkdir(parents=True, exist_ok=True)
        for i, (x, p) in enumerate(zip(r.parity_x[:LITE_ROWS], r.parity_p[:LITE_ROWS])):
            (out / f"row{i}.bin").write_bytes(np.ascontiguousarray(x.reshape(1, -1), np.float32).tobytes())
            (out / f"row{i}.txt").write_text(f"@OUTPUT@ 2 1 1\n{float(p)!r}\n")


# --- timing and files -------------------------------------------------------------------------

def time_onnx(data: bytes, row: np.ndarray) -> dict:
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = opts.inter_op_num_threads = 1
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    sess = ort.InferenceSession(data, opts, providers=["CPUExecutionProvider"])
    x = row.astype(np.float32).reshape(1, -1)
    for _ in range(WARMUP):
        sess.run(None, {"x": x})
    lat = np.empty(TIMED)
    for i in range(TIMED):
        t0 = timeit.default_timer()
        sess.run(None, {"x": x})
        lat[i] = timeit.default_timer() - t0
    return {"intra_op_num_threads": 1, "inter_op_num_threads": 1, "execution_mode": "sequential",
            "warmup_calls": WARMUP, "timed_calls": TIMED, "median_us": round(float(np.median(lat)) * 1e6, 2),
            "p99_us": round(float(np.percentile(lat, 99)) * 1e6, 2), "platform_machine": platform.machine(),
            "onnxruntime": ort.__version__}


def packages() -> dict:
    return {name: importlib_metadata.version(name) for name in PACKAGES}


def write_outputs(out: Path, built: Built, train, val, skipped, byte_reproducible: bool) -> dict:
    """Files for the shipped variants; every file of a variant that is not exported is removed."""
    out.mkdir(parents=True, exist_ok=True)
    variants, exports, timing = {}, {}, {}
    for tag, r in built.results.items():
        for names in (FILES, MINDIR):
            (out / names[tag]).unlink(missing_ok=True)
        (out / f"{Path(MINDIR[tag]).stem}.ms").unlink(missing_ok=True)  # a stale hand-made .ms
        meta = dict(r.meta, files=None)
        if r.onnx is not None:
            _write(out / FILES[tag], r.onnx)
            _write(out / MINDIR[tag], r.mindir)
            meta["files"] = {"onnx": FILES[tag], "mindir": MINDIR[tag]}
            exports[tag] = {"mindir": MINDIR[tag], **r.exports}
            timing[tag] = time_onnx(r.onnx, r.timing_row)
        variants[tag] = meta
    top = {
        "name": "tier_c/student_vitals", "synthetic": True, "validated": False,
        "framework": "MindSpore", "framework_version": ms.__version__, "device": "CPU", "mode": "GRAPH_MODE",
        "threads_num": student.MS_THREADS, "seed": 0, "shuffle": "numpy.random.default_rng(0) each epoch",
        "packages": packages(), "python": platform.python_version(), "byte_reproducible": byte_reproducible,
        "seeds_read": {"train": [t.seed for t in train], "validation": [t.seed for t in val]},
        "seeds_skipped_unread": skipped, "grid": GRID.as_dict(), "budget_cuts": built.cuts,
        "variants": variants,
    }
    meta_bytes = _json_bytes(top)
    _write(out / "metadata.json", meta_bytes)
    _write(out / "exports.json", _json_bytes({
        "metadata_sha256": sha256(meta_bytes),  # evaluate_student refuses a metadata.json that was edited
        "variants": exports,
        "lite": "made by hand with convert_lite.sh; see lite.txt when present",
        "note": "MindIR bytes are not reproducible between runs (a timestamp inside): they are checked by sha256 here",
    }))
    _write(out / "timing.json", _json_bytes({**timing, "run": built.seconds}))
    return top


def run_log(top: dict, built: Built, run_id: str | None, commit: str | None) -> dict:
    """Everything about the run that is worth keeping and small: no per-step values. Everything outside
    `time` is deterministic, so two runs give the same bytes once `time` is removed."""
    variants, times = {}, {"fits": {}, "references": {}, "total_seconds": built.seconds.get("build_s")}
    data = {"train_seeds": top["seeds_read"]["train"], "validation_seeds": top["seeds_read"]["validation"],
            "skipped_unread": top["seeds_skipped_unread"], "variants": {}}
    for tag, r in built.results.items():
        m = r.meta
        data["variants"][tag] = r.runlog["rows"]
        variants[tag] = {"fits": r.runlog["fits"], "references": {"constant": m["references"]["constant"], **r.runlog["references"]},
                         "teacher_gate": m["teacher_gate"], "chosen": {"teacher": {**m["teacher"]["chosen"], "fit_index": r.runlog["fit_index"]["teacher"]},
                                                                         "student": None if m["student"] is None else {**m["student"], "fit_index": r.runlog["fit_index"]["student"]},
                                                                         "shipped": m["shipped"], "distilled": m["distilled"]},
                         "thresholds": m["thresholds"], "parameters": m["parameters"]}
        times["fits"].update(r.times["fits"])
        times["references"].update({f"{tag} {k}": v for k, v in r.times["references"].items()})
    return {
        "model": top["name"], "run_id": run_id, "commit": commit, "framework": top["framework"],
        "framework_version": top["framework_version"], "packages": top["packages"],
        "settings": {"grid": top["grid"], "optimiser": {"name": "Adam", "learning_rate": GRID.learning_rate,
                                                          "weight_decay": "on weight matrices only"},
                     "losses": {"teacher": "BCEWithLogits on the true labels",
                                "distilled": "MSE on the teacher's clipped logits", "hard_label": "BCEWithLogits"},
                     "threads_num": top["threads_num"], "seed": top["seed"], "shuffle": top["shuffle"],
                     "mode": top["mode"], "device": top["device"],
                     "bootstrap": {"resamples": student.bootstrap.RESAMPLES, "seed": student.bootstrap.SEED}},
        "data": data, "variants": variants, "budget_cuts": built.cuts, "time": times,
    }


def _check_ids(run_id: str | None, commit: str | None) -> None:
    if run_id is not None and not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", run_id):
        raise TrialError("--run_id may only hold A-Z a-z 0-9 . _ - and at most 64 characters")
    if commit is not None and not re.fullmatch(r"[\x21-\x7e]{1,128}", commit):
        raise TrialError("--commit must be 1 to 128 printable characters without spaces")


def summary(top: dict) -> list[str]:
    lines = [f"MindSpore {top['framework_version']}, seeds trained {top['seeds_read']['train'][0]}-"
             f"{top['seeds_read']['train'][-1]} ({len(top['seeds_read']['train'])} trials), validated on "
             f"{len(top['seeds_read']['validation'])} trials; budget cuts: {', '.join(top['budget_cuts']) or 'none'}"]
    for tag, m in top["variants"].items():
        ref, gate = m["references"], m["teacher_gate"]
        lines.append(
            f"{tag}: validation log loss teacher {m['teacher']['val_log_loss']}, constant {ref['constant']['val_log_loss']}, "
            f"old recipe {ref['old_recipe']['val_log_loss']}, hard-label network {ref['hard_label_mlp']['val_log_loss']}, "
            f"regularised boosting {None if ref['regularised_gradient_boosting'] is None else ref['regularised_gradient_boosting']['val_log_loss']}; "
            f"teacher gate {'passed' if gate['passes'] else 'FAILED'}; shipped {m['shipped']}"
            + ("" if m["student"] is None else
               f" ({m['student']['inputs']}, width {m['student']['width']}, {m['student']['parameters']} parameters, "
               f"validation log loss {m['student']['val_log_loss']}, teacher has {m['parameters']['teacher']}); "
               f"thresholds student {m['thresholds']['student']['threshold']}, teacher {m['thresholds']['teacher']['threshold']}; "
               f"onnx sha256 {m['onnx_sha256']}"))
    lines.append("synthetic, not validated: trained on the same simulator it will be judged on")
    return lines


def main(argv: list[str]) -> int:
    log = lambda s: print(s, flush=True)  # noqa: E731
    try:
        a = _parser().parse_args(argv)
        if a.data_url and a.folders:
            raise TrialError("give trial folders or --data_url, not both")
        if a.out and a.train_url and Path(a.out) != Path(a.train_url):
            raise TrialError("--out and --train_url name different folders")
        folders = a.folders
        if a.data_url:
            if not Path(a.data_url).is_dir():
                raise TrialError("--data_url is not a folder")
            folders = sorted(str(p) for p in Path(a.data_url).iterdir() if p.is_dir())
        if a.workers is not None and a.workers < 0:
            raise TrialError("--workers needs a whole number")
        _check_ids(a.run_id, a.commit)
        out = Path(a.out or a.train_url or OUT)
        train, val, skipped = load_for_training(folders, a.cache, a.workers)
        built = build(train, val, GRID, log=log)
        top = write_outputs(out, built, train, val, skipped, a.byte_reproducible == "true")
        if a.lite_rows:
            write_lite_rows(Path(a.lite_rows), built)
        if a.run_log:
            Path(a.run_log).parent.mkdir(parents=True, exist_ok=True)
            _write(Path(a.run_log), _json_bytes(run_log(top, built, a.run_id, a.commit)))
    except TrialError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    for line in summary(top):
        log(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
