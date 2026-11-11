"""Held-out rows for the distilled vitals student, next to NEWS2 and the baseline.

    python -m research.evaluate_student <trial folder>...

Retrains on seeds 1 to 10 (deterministic; held-out folders are skipped unread by the training
loader), refuses unless the retrained ONNX bytes match the committed files and metadata.json,
then runs `research.evaluate` once with the extra rows. The student rows run the committed ONNX
files through onnxruntime. Exit 2 with one line on any refusal.
"""

import json
import sys

from . import evaluate
from .alerts import POLICIES
from .baseline_policy import NO_TEMP, baseline_policies
from .student import Scorer, build, load_for_training, onnx_predict, prob_policy, session, sha256
from .train_student import FILES, OUT
from .trials import TrialError

MAX_FILE_BYTES = 1 << 20


def _read(name: str) -> bytes:
    try:
        with open(OUT / name, "rb") as f:
            data = f.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise TrialError(f"{name}: cannot be read (run research.train_student first)") from None
    if len(data) > MAX_FILE_BYTES:
        raise TrialError(f"{name}: larger than {MAX_FILE_BYTES} bytes")
    return data


def student_policies(result: dict, meta: dict) -> tuple[tuple, list[str]]:
    policies, notes = [], []
    for tag, r in result.items():
        m = meta["models"][tag]
        data = _read(FILES[tag])
        if not (sha256(data) == sha256(r["onnx"]) == m["onnx_sha256"]):
            raise TrialError(f"{FILES[tag]}: retrained bytes, file and metadata.json disagree on sha256")
        sfx = "" if tag == "all" else "_no_temp"
        teacher, sess = r["teacher"], session(data)
        t_t, s_t = r["meta"]["threshold_teacher"], r["meta"]["threshold_student"]
        if t_t is not None:
            policies.append(prob_policy(f"teacher{sfx}", Scorer(lambda x, t=teacher: t.predict_proba(x)[:, 1], r["temp"]), t_t))
        if s_t is not None:
            sc = Scorer(lambda x, s=sess: onnx_predict(s, x), r["temp"])
            policies += [prob_policy(f"student{sfx}", sc, s_t), prob_policy(f"news2_or_student{sfx}", sc, s_t, True)]
        notes.append(f"{tag}: {m['student_kind']} student, {m['n_features']} features, threshold student "
                     f"{s_t}, teacher {t_t} (matched to NEWS2 alerts on seeds 8-10; none = no row), "
                     f"onnx sha256 {m['onnx_sha256']}")
    return tuple(policies), notes


def stuck_notes(val: list, policies: tuple) -> list[str]:
    """Label (never choose): a row whose time in alert on seeds 8-10 exceeds NEWS2's is not a
    working rule, because one open alert hides how long the patient is alerting."""
    pool = evaluate.Pool()
    for trial in val:
        evaluate.score_trial(trial, (POLICIES[0], *policies), pool)
    share = lambda name: pool.tallies[name].on_bins / pool.tallies[name].bins  # noqa: E731
    chart = share(POLICIES[0].name)
    out = []
    for p in policies:
        own = p.name.removeprefix("news2_or_")  # an OR row is judged by its own model's alerting
        s = share(own)
        verdict = (f"no usable threshold: alert stuck open (about {s:.0%} of time on seeds 8-10); "
                   "not a working rule") if s > chart else "time in alert within NEWS2's"
        out.append(f"{p.name}: {own} time in alert on seeds 8-10 {s:.3f}, NEWS2 {chart:.3f}: {verdict}")
    return out


def run(folders: list[str]) -> str:
    train, val, _ = load_for_training(folders)
    result = build(train, val)
    try:
        meta = json.loads(_read("metadata.json"))
    except ValueError:
        raise TrialError("metadata.json: not valid JSON") from None
    extra, notes = student_policies(result, meta)
    notes += stuck_notes(val, extra)
    params, _ = evaluate._baseline_params()
    policies = (POLICIES + baseline_policies(params.k, params.p)
                + baseline_policies(params.k, params.p, "_no_temp", NO_TEMP) + extra)
    text = evaluate.run(folders, policies)
    head = ["student rows (slice B5): trained on seeds 1-7, chosen on 8-10, scored here once on 11-30",
            *notes, ""]
    return "\n".join(head) + "\n" + text


def main(argv: list[str]) -> int:
    try:
        text = run(argv)
    except TrialError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
