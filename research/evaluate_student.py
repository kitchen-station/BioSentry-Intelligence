"""Held-out rows for the distilled vitals student (slice M1), next to NEWS2 and the baseline.

    python -m research.evaluate_student <trial folder>... [--cache <dir>] [--workers N] [--json <held-out.json>]

Give the ward-v2 folders of seeds 11 to 30 (scored once) and the ward-v2-ext folders of seeds 61
to 160 (to refit on), and, when metadata.json says `byte_reproducible: false`, also the ward-v2-ext
folders of seeds 261 to 300. It refits ONLY the chosen teacher and student, read from
metadata.json, and refuses on any mismatch of the weight sha256 or the ONNX sha256 with metadata.json
and the committed file. With `byte_reproducible: false` it checks the committed files by sha256 only
and re-scores the committed ONNX on validation (log loss within 1e-6 of metadata.json). Then it runs
`research.evaluate` once with the extra rows: the teacher scores with the MindSpore network, the
student rows run the committed ONNX through onnxruntime. Ward-v2 seeds 1 to 10 are refused; ext
seeds 301-340 and 31-60 are skipped after reading only their manifest.json. Exit 2 with one line on
any refusal.
"""

import json
import sys
from pathlib import Path

from . import evaluate
from .alerts import POLICIES
from .baseline_policy import NO_TEMP, baseline_policies
from .student import (
    VAL_SEEDS, Cell, Grid, Scorer, _check_shape, _manifest, _stack, extract_all, forward, load_for_evaluation, log_loss,
    onnx_predict, prob_policy, refit_variant, session, sha256, to_onnx, weights_sha256,
)
from .train_student import FILES, MINDIR, OUT, _parser
from .trials import TrialError

MAX_FILE_BYTES = 1 << 20
VAL_LOSS_TOL = 1e-6


def _read(out: Path, name: str, required: bool = True) -> bytes | None:
    try:
        with open(out / name, "rb") as f:
            data = f.read(MAX_FILE_BYTES + 1)
    except OSError:
        if not required:
            return None
        raise TrialError(f"{name}: cannot be read (run research.train_student first)") from None
    if len(data) > MAX_FILE_BYTES:
        raise TrialError(f"{name}: larger than {MAX_FILE_BYTES} bytes")
    return data


def _json(out: Path, name: str) -> dict:
    try:
        return json.loads(_read(out, name))
    except ValueError:
        raise TrialError(f"{name}: not valid JSON") from None


def _check_files(out: Path) -> None:
    """metadata.json and the committed MindIR files against exports.json (MindIR bytes are not
    reproducible between runs, so exports.json is their reference)."""
    exports_file = _json(out, "exports.json")
    if sha256(_read(out, "metadata.json")) != exports_file["metadata_sha256"]:
        raise TrialError("metadata.json: sha256 differs from exports.json (the file was changed after training)")
    for tag, e in exports_file["variants"].items():
        data = _read(out, MINDIR[tag])
        if sha256(data) != e["mindir_sha256"] or len(data) != e["mindir_bytes"]:
            raise TrialError(f"{MINDIR[tag]}: sha256 or size differs from exports.json")


def _validation_log_loss(sess, val, temp: bool, kind: str) -> float:
    x, y, _ = _stack(val, kind, temp)
    return log_loss(y, onnx_predict(sess, x))


def student_policies(train, val, meta: dict, out: Path) -> tuple[tuple, list[str]]:
    policies, notes = [], []
    exact, g = meta["byte_reproducible"], Grid.from_dict(meta["grid"])
    for tag, m in meta["variants"].items():
        refit = refit_variant(train, m["temperature"], m, g)
        teacher = refit["teacher"]
        sfx = "" if tag == "all" else "_no_temp"
        if exact and weights_sha256(teacher) != m["teacher"]["weights_sha256"]:
            raise TrialError(f"{tag}: the refitted teacher's weight sha256 differs from metadata.json")
        t_thr = m["thresholds"]["teacher"]["threshold"]
        if t_thr is not None:
            cell = Cell(teacher)
            policies.append(prob_policy(f"teacher{sfx}", Scorer(lambda x, c=cell: forward(c, x), m["temperature"],
                                                                "teacher"), t_thr))
        s_thr = m["thresholds"]["student"]["threshold"] if m["student"] else None
        if m["student"] is None:
            notes.append(f"{tag}: nothing shipped (no network beat the constant): no student rows")
        else:
            data = _read(out, FILES[tag])
            kind = m["student"]["inputs"]
            if sha256(data) != m["onnx_sha256"]:
                raise TrialError(f"{FILES[tag]}: the committed file's sha256 differs from metadata.json")
            sess = session(data)
            if exact:
                if weights_sha256(refit["student"]) != m["weights_sha256"]:
                    raise TrialError(f"{tag}: the refitted student's weight sha256 differs from metadata.json")
                if sha256(to_onnx(refit["student"])) != m["onnx_sha256"]:
                    raise TrialError(f"{tag}: the refitted ONNX sha256 differs from metadata.json")
            else:
                loss = _validation_log_loss(sess, val, m["temperature"], kind)
                if abs(loss - m["student"]["val_log_loss_onnx"]) > VAL_LOSS_TOL:
                    raise TrialError(f"{FILES[tag]}: validation log loss {loss:.6f} differs from metadata.json "
                                     f"{m['student']['val_log_loss_onnx']} by more than {VAL_LOSS_TOL}")
            if s_thr is not None:
                sc = Scorer(lambda x, s=sess: onnx_predict(s, x), m["temperature"], kind)
                policies += [prob_policy(f"student{sfx}", sc, s_thr), prob_policy(f"news2_or_student{sfx}", sc, s_thr, True)]
        label = "no usable threshold on seeds 261-300 (no t keeps both its alerts and its time in alert within NEWS2's): no row"
        notes.append(f"{tag}: {m['shipped']}, {m['features_student'] and len(m['features_student'])} student inputs; "
                     f"threshold student {s_thr if s_thr is not None else label}; "
                     f"teacher {t_thr if t_thr is not None else label}; onnx sha256 {m['onnx_sha256']}")
    return tuple(policies), notes


def run(folders: list[str], out: Path = OUT, cache: str | None = None, workers: int | None = None,
        result: dict | None = None) -> str:
    train, held = load_for_evaluation(folders, cache, workers)
    if not held:
        raise TrialError("no ward-v2 folder of seeds 11 to 30 given")
    meta = _json(out, "metadata.json")
    if meta.get("framework") != "MindSpore":
        raise TrialError("metadata.json: not a MindSpore student")
    _check_files(out)
    val = []
    if not meta["byte_reproducible"]:
        ext = []
        for f in sorted(folders):
            seed, m = _manifest(f)
            if seed in VAL_SEEDS:
                _check_shape(f, m)
                ext.append(f)
        if not ext:
            raise TrialError("metadata.json says byte_reproducible is false: give the ward-v2-ext folders of "
                             "seeds 261 to 300 too")
        val = extract_all(ext, cache, workers)
    extra, notes = student_policies(train, val, meta, out)
    params, _ = evaluate._baseline_params()
    policies = (POLICIES + baseline_policies(params.k, params.p)
                + baseline_policies(params.k, params.p, "_no_temp", NO_TEMP) + extra)
    text = evaluate.run(held, policies, result)
    mode = ("refitted the chosen teacher and student and matched their weight and ONNX sha256 to metadata.json"
            if meta["byte_reproducible"] else
            "byte_reproducible is false: the committed files matched by sha256 and the committed ONNX re-scored "
            "on seeds 261-300; the teacher was refitted without a bytes check")
    head = [f"student rows (slice M1, MindSpore {meta['framework_version']}): trained on seeds 61-160, "
            "chosen on 261-300, scored here once on ward-v2 seeds 11-30", mode, *notes, ""]
    return "\n".join(head) + "\n" + text


def main(argv: list[str]) -> int:
    try:
        argv, json_path = evaluate.pop_json(argv)
        evaluate.check_json_path(json_path)  # before any trial is read
        a = _parser().parse_args(argv)
        result: dict = {}
        text = run(a.folders, Path(a.out) if a.out else OUT, a.cache, a.workers, result if json_path else None)
        if json_path:
            evaluate.write_json(json_path, result)
    except TrialError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
