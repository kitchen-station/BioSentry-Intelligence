"""A split-conformal confidence check on the distilled student (slice M3).

    python -m research.confidence calibrate <ward-v2-ext folders of seeds 301-340>... [--model <dir>]
                                  [--out <dir>] [--cache <dir>] [--workers N]
                                  [--run_log <file>] [--run_id <text>] [--commit <sha>]
    python -m research.confidence evaluate <ward-v2 folders of seeds 11-30>... [--model <dir>] [--out <dir>]
                                  [--json <held-out.json>] [--held-out-set 1|2]

The student alerts when p >= t. The check lets that alert through only when p > q, where q is the
finite-sample quantile of one randomly drawn negative bin per calibration stay; otherwise the bin is
"not sure" and never alerts. The core (quantile, check, draw, the calibration file, the policy) uses the
standard library only; numpy, onnxruntime and the student are imported inside the two commands.
Exit 2 with one line on any refusal. Contract: README "The confidence check (slice M3)".
"""

import argparse
import hashlib
import json
import math
import os
import platform
import re
import sys
import timeit
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from fractions import Fraction
from importlib import metadata as importlib_metadata
from pathlib import Path

from .alerts import POLICIES, Policy
from .bootstrap import SplitMix64
from .trials import Row, TrialError

ALPHA = "0.01"  # fixed before any data was used; there is no sweep
DRAW_SEED = 20261113
CAL_SEEDS = range(301, 341)
HELD_SEEDS = range(11, 31)
MAX_BYTES = 64 * 1024
OUT = Path(__file__).parent.parent / "models" / "tier_c" / "confidence_check"
MODEL = Path(__file__).parent.parent / "models" / "tier_c" / "student_vitals"
ONNX = {"all": "model.onnx", "no_temp": "model_no_temp.onnx"}
CALIBRATION = {"all": "calibration.json", "no_temp": "calibration_no_temp.json"}
HELD_SEEDS_2 = range(1001, 1401)  # held-out set 2 (research/evaluate.py HELD_OUT_2)
HELD_OUTPUT = "heldout.txt"
HELD_OUTPUT_2 = "heldout_2.txt"  # its own run-once guard; set 1's file is untouched
CHART = POLICIES[0]
SUMMARY_POINTS = 200
PACKAGES = ("numpy", "onnxruntime")


# --- the pure core ------------------------------------------------------------------------------

def indices(n: int, alpha: str = ALPHA) -> tuple[int, int]:
    """(j, k): j = floor(alpha (n + 1)) in exact arithmetic, k = n + 1 - j = ceil((n + 1)(1 - alpha))."""
    j = int(Fraction(alpha) * (n + 1) // 1)
    return j, n + 1 - j


def quantile(scores: Sequence[float], alpha: str = ALPHA) -> tuple[int, int, int, float | None]:
    """(n, j, k, q): q is the k-th smallest score, or None (+inf) when j = 0 (n too small)."""
    n = len(scores)
    j, k = indices(n, alpha)
    return n, j, k, (sorted(scores)[k - 1] if j > 0 else None)


def check(p: float, t: float, q: float | None) -> str:
    """"alert" only when the student fires AND p is strictly above q; q None means +inf."""
    if p < t:
        return "quiet"
    return "alert" if q is not None and p > q else "not sure"


def draw(stays: Sequence[Sequence[float]], seed: int = DRAW_SEED) -> list[float]:
    """One negative-bin score per stay, uniformly ("subsampling once", Dunn, Wasserman, Ramdas). `stays`
    is visited by seed in sorted order and, within a trial, in episodes.csv order (not sorted by patient).
    The guarantee does not depend on that order: any order fixed before the scores are seen gives one
    uniform draw per stay. A stay with no negative scored bin takes no draw."""
    rng = SplitMix64(seed)
    return [s[rng.next() % len(s)] for s in stays if len(s)]


def spread(alpha: str, n: int) -> float:
    return 2 * math.sqrt(float(Fraction(alpha) * (1 - Fraction(alpha))) / n)


def verdict(interval: tuple[float, float] | None, alpha: str, n: int) -> tuple[bool, bool]:
    """(met, conservative). MET iff the interval's lower end <= alpha + d, d = 2 sqrt(alpha (1 - alpha) / n);
    conservative iff its upper end < alpha - d. No interval (no stays): not met."""
    if interval is None:
        return False, False
    a, d = float(Fraction(alpha)), spread(alpha, n)
    return interval[0] <= a + d, interval[1] < a - d


def summary_quantiles(scores: Sequence[float], m: int = SUMMARY_POINTS) -> list[float]:
    """The sorted scores thinned to at most m evenly spaced order statistics (for plotting)."""
    s = sorted(scores)
    if len(s) <= m:
        return s
    return [s[round(i * (len(s) - 1) / (m - 1))] for i in range(m)]


@dataclass(frozen=True, slots=True)
class Calibration:
    alpha: str
    draw_seed: int
    variant: str
    seeds: list[int]
    negative_bins: int  # negative scored bins before the one-per-stay draw
    n: int  # stays drawn from (one score each)
    j: int
    k: int
    q: float | None  # None: +inf, the check never supports an alert
    t: float
    onnx_sha256: str
    days: int
    speed: int
    scenario_sha256: str

    @property
    def binding(self) -> bool:
        return self.q is None or self.q >= self.t


def make(scores: Sequence[float], alpha: str, **meta) -> Calibration:
    n, j, k, q = quantile(scores, alpha)
    return Calibration(alpha=alpha, n=n, j=j, k=k, q=q, **meta)


def _bytes(cal: Calibration) -> bytes:
    return (json.dumps(asdict(cal), indent=1, sort_keys=True) + "\n").encode()


def _atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def save(cal: Calibration, path: Path) -> str:
    """Writes the JSON and `<path>.sha256`; returns the hash."""
    data = _bytes(cal)
    h = hashlib.sha256(data).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic(path, data)
    _atomic(path.with_name(path.name + ".sha256"), (h + "\n").encode())
    return h


def _read(path: Path, what: str) -> bytes:
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_BYTES + 1)
    except OSError:
        raise TrialError(f"{what}: cannot be read (run research.confidence calibrate first)") from None
    if len(data) > MAX_BYTES:
        raise TrialError(f"{what}: larger than {MAX_BYTES} bytes")
    return data


def load(path: Path) -> tuple[Calibration, str]:
    """(calibration, sha256 of the file). Refuses a size over 64 KiB, a sha256 that differs from the
    sibling file, alpha or the draw seed other than the constants, an unknown or missing field, a wrong
    type, or j, k, q that do not follow from n."""
    data = _read(path, path.name)
    h = hashlib.sha256(data).hexdigest()
    if _read(path.with_name(path.name + ".sha256"), path.name + ".sha256").decode(errors="replace").strip() != h:
        raise TrialError(f"{path.name}: sha256 differs from {path.name}.sha256 (the file was changed)")
    try:
        obj = json.loads(data)
    except (ValueError, RecursionError):
        raise TrialError(f"{path.name}: not valid JSON") from None
    names = {f.name for f in fields(Calibration)}
    if not isinstance(obj, dict) or set(obj) != names:
        raise TrialError(f"{path.name}: unknown or missing field")
    ints = ("draw_seed", "negative_bins", "n", "j", "k", "days", "speed")
    strs = ("alpha", "variant", "onnx_sha256", "scenario_sha256")
    if (any(type(obj[k]) is not int for k in ints) or any(type(obj[k]) is not str for k in strs)
            or type(obj["t"]) not in (int, float) or not isinstance(obj["seeds"], list)
            or any(type(s) is not int for s in obj["seeds"]) or obj["q"] is not None and type(obj["q"]) not in (int, float)):
        raise TrialError(f"{path.name}: a field has the wrong type")
    cal = Calibration(**obj)
    if cal.alpha != ALPHA:
        raise TrialError(f"{path.name}: alpha {cal.alpha} is not the fixed {ALPHA}")
    if cal.draw_seed != DRAW_SEED:
        raise TrialError(f"{path.name}: draw seed {cal.draw_seed} is not the fixed {DRAW_SEED}")
    if cal.n < 0 or indices(cal.n) != (cal.j, cal.k) or (cal.q is None) != (cal.j == 0):
        raise TrialError(f"{path.name}: j, k or q do not follow from n")
    return cal, h


def verify(cal: Calibration, *, variant: str, onnx_sha256: str, t: float, shape: tuple[int, int, str]) -> None:
    """The file must belong to this ONNX, this threshold and this simulator setting."""
    if cal.variant != variant:
        raise TrialError(f"calibration is for variant {cal.variant}, not {variant}")
    if cal.onnx_sha256 != onnx_sha256:
        raise TrialError(f"{variant}: the calibration's ONNX sha256 differs from the model's")
    if cal.t != t:
        raise TrialError(f"{variant}: the calibration's threshold {cal.t} differs from the model's {t}")
    if (cal.days, cal.speed, cal.scenario_sha256) != shape:
        raise TrialError(f"{variant}: days, speed or scenario hash differ from the calibration's "
                         "(the guarantee cannot be claimed across different settings)")


def checked_policy(name: str, scorer: Callable[[Mapping[int, Row]], dict[int, float]], t: float,
                   q: float | None, with_chart: bool = False) -> Policy:
    """The same shape as student.prob_policy. The student's state is `check(...) == "alert"` and nothing
    else, so a "not sure" bin cannot alert (only the NEWS2 chart can, with `with_chart`)."""
    def states(rows: Mapping[int, Row]) -> dict[int, bool]:
        p = scorer(rows)
        return {m: (m in p and check(p[m], t, q) == "alert") or (with_chart and CHART.alerting(rows[m]))
                for m in rows}

    return Policy(name, lambda row: False, CHART.abstains, states)


# --- the commands -------------------------------------------------------------------------------

def usable(m: Mapping) -> str | None:
    """Why a variant has no check (None: it has one): no exported student or no usable threshold."""
    if not m.get("student") or not m.get("onnx_sha256") or not m.get("files"):
        return "no student was exported"
    if m["thresholds"]["student"]["threshold"] is None:
        return "the student has no usable threshold"
    return None


def _model(model: Path) -> tuple[dict, dict[str, tuple[bytes, Mapping]]]:
    """metadata.json and, per usable variant, the ONNX bytes (sha256 checked against metadata.json)."""
    from .evaluate_student import _check_files, _json, _read as read_file
    from .student import sha256

    try:
        _check_files(model)  # metadata.json against exports.json, as evaluate_student does
        meta = _json(model, "metadata.json")
        if meta.get("framework") != "MindSpore":
            raise TrialError("metadata.json: not a MindSpore student")
        found = {}
        for tag, m in meta["variants"].items():
            if tag not in ONNX:
                raise TrialError(f"metadata.json: unknown variant {tag}")
            if usable(m) is None:
                bool(m["temperature"]), m["student"]["inputs"]  # touched here so a bad shape refuses now
                data = read_file(model, ONNX[tag])
                if sha256(data) != m["onnx_sha256"]:
                    raise TrialError(f"{ONNX[tag]}: sha256 differs from metadata.json")
                found[tag] = (data, m)
    except TrialError:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, IndexError) as e:
        raise TrialError(f"metadata.json: unexpected shape ({type(e).__name__})") from None
    return meta, found


def _manifests(folders: Sequence[str], allowed: range, what: str) -> list[tuple[int, str, dict]]:
    """(seed, folder, manifest) sorted by seed. Only manifest.json is opened here: any seed outside
    `allowed`, a seed given twice or a different setting is refused before any other file is."""
    from .student import _check_shape, _manifest

    if not folders:
        raise TrialError("no trial folders given")
    out, seen = [], set()
    for folder in sorted(folders):
        seed, m = _manifest(folder)
        name = Path(folder).name[:40]
        if seed in seen:
            raise TrialError(f"{name}: seed {seed} given twice")
        seen.add(seed)
        if seed not in allowed:
            raise TrialError(f"{name}: seed {seed} is not in {what} seeds {allowed.start} to {allowed.stop - 1}")
        _check_shape(folder, m)
        out.append((seed, folder, m))
    return sorted(out, key=lambda x: x[0])


def _negative_stays(tf, p) -> list[list[float]]:
    """Per stay of one trial, its negative scored bins' scores in minute order (starting patients are
    y = -2 and bins after fully in y = -1, so neither is here)."""
    import numpy as np

    idx = np.flatnonzero(tf.y == 0)
    cuts = np.flatnonzero(np.diff(tf.pid[idx])) + 1
    return [g.tolist() for g in np.split(p[idx], cuts)] if len(idx) else []


def calibrate(folders: Sequence[str], model: Path = MODEL, out: Path = OUT, cache: str | None = None,
              workers: int | None = None, run_log: Path | None = None, run_id: str | None = None,
              commit: str | None = None, seeds: range = CAL_SEEDS) -> list[str]:
    """`seeds` is the exact set required (the contract's 301-340; a test may give a smaller one)."""
    start = timeit.default_timer()
    _check_ids(run_id, commit)
    jobs = _manifests(folders, CAL_SEEDS, "calibration")
    if [s for s, _, _ in jobs] != list(seeds):
        raise TrialError(f"calibration needs exactly seeds {seeds.start} to {seeds.stop - 1}, each once")
    from .student import extract_all, onnx_predict, session, view

    meta, found = _model(model)
    notes = [f"{tag}: not applicable ({usable(m)}); no calibration file" for tag, m in meta["variants"].items()
             if tag not in found]
    shape = (jobs[0][2]["days"], jobs[0][2]["speed"], jobs[0][2]["scenario_sha256"])
    tfs = extract_all([f for _, f, _ in jobs], cache, workers)
    if [tf.seed for tf in tfs] != list(seeds):
        raise TrialError("a seed changed while reading")
    seeds = list(seeds)
    log_variants = {}
    for tag, (data, m) in found.items():
        sess, t = session(data), m["thresholds"]["student"]["threshold"]
        stays = []
        for tf in tfs:
            stays += _negative_stays(tf, onnx_predict(sess, view(tf.x, m["temperature"], m["student"]["inputs"])))
        drawn = draw(stays)
        cal = make(drawn, ALPHA, draw_seed=DRAW_SEED, variant=tag, seeds=seeds, negative_bins=sum(map(len, stays)),
                   t=t, onnx_sha256=m["onnx_sha256"], days=shape[0], speed=shape[1], scenario_sha256=shape[2])
        digest = save(cal, out / CALIBRATION[tag])
        notes.append(f"{tag}: n={cal.n} j={cal.j} k={cal.k} q={cal.q} t={t} "
                     f"{'binding' if cal.binding else 'not binding (q < t)'}; {CALIBRATION[tag]} sha256 {digest}")
        log_variants[tag] = {**asdict(cal), "binding": cal.binding, "calibration_sha256": digest,
                             "score_quantiles": summary_quantiles(drawn)}
    if run_log is not None:
        log = {"model": "tier_c/confidence_check", "run_id": run_id, "commit": commit, "alpha": ALPHA,
               "draw_seed": DRAW_SEED, "calibration_seeds": seeds, "variants": log_variants,
               "not_applicable": {t: usable(m) for t, m in meta["variants"].items() if t not in found},
               "packages": {n: importlib_metadata.version(n) for n in PACKAGES}, "python": platform.python_version(),
               "time": {"seconds": timeit.default_timer() - start}}
        run_log.parent.mkdir(parents=True, exist_ok=True)
        _atomic(run_log, (json.dumps(log, indent=1, sort_keys=True) + "\n").encode())
    return notes


def _block(tag: str, cal: Calibration, files: Sequence[str], scorer, student_policy: Policy, checked: Policy,
           data: dict | None = None, label: str = "held-out ward-v2 seeds 11-30") -> list[str]:
    """The contract's held-out block for one variant, from the trials' rows and the student's p. When `data`
    is a dict it is filled with the same numbers at full precision (the --json file)."""
    import numpy as np

    from . import bootstrap
    from .evaluate import _any
    from .events import windows
    from .alerts import scan
    from .student import Scorer, patient_labels
    from .trials import load_trial

    t, q = cal.t, cal.q
    shares, bins = [], {"scored": 0, "neg": 0, "neg_q": 0, "neg_chk": 0, "neg_t": 0, "alert": 0, "sure": 0}
    alerts = {"student": [0, 0], "checked": [0, 0]}  # [new alerts, alerting bins]
    lost = {s: {k: 0 for k in ("any", "early")} for s in ("all", "uncut")}
    for folder in files:
        trial = load_trial(folder)  # one trial at a time: rows of all 20 would not fit in memory
        by: dict[str, dict[int, Row]] = {}
        for r in trial.rows:
            by.setdefault(r.patient, {})[r.minute] = r
        wins, _ = windows(trial)
        window_of = {w.patient: w for w in wins}
        for e in trial.episodes:
            rows = by.get(e.patient, {})
            p = scorer(rows)
            minutes = sorted(p)
            pv = np.array([p[m] for m in minutes], dtype=np.float64)
            fire = pv >= t
            sure = fire & (pv > q if q is not None else np.zeros(len(pv), bool))
            bins["scored"] += len(pv)
            bins["alert"] += int(fire.sum())
            bins["sure"] += int(sure.sum())
            if e.admit_minute != 0:
                neg = patient_labels(minutes, e) == 0
                if neg.any():
                    n_neg = int(neg.sum())
                    above = (pv > q) if q is not None else np.zeros(len(pv), bool)
                    shares.append((int((neg & above).sum()) / n_neg, int((neg & sure).sum()) / n_neg,
                                   int((neg & fire).sum()) / n_neg, 1.0))
                    bins["neg"] += n_neg
                    bins["neg_q"] += int((neg & above).sum())
                    bins["neg_chk"] += int((neg & sure).sum())
                    bins["neg_t"] += int((neg & fire).sum())
            res = {"student": scan(student_policy, rows), "checked": scan(checked, rows)}
            for name, (fired, on) in res.items():
                alerts[name][0] += len(fired)
                alerts[name][1] += len(on)
            w = window_of.get(e.patient)
            if w:
                for scope in ("all",) + (() if w.cut else ("uncut",)):
                    for kind, hit in (("any", lambda f, o: _any(o, w.start, w.end)),
                                      ("early", lambda f, o: any(w.start <= m < w.full_in for m in f))):
                        lost[scope][kind] += hit(*res["student"]) and not hit(*res["checked"])
        Scorer.clear()
        scorer._memo.clear()
        del trial, by, wins  # before the next load: never two trials at once
    stats = {name: (lambda s, c=c: s[c] / s[3] if s[3] else None) for c, name in enumerate(("error", "checked", "student"))}
    iv = bootstrap.intervals({"stays": shares}, stats)
    mean = {k: (sum(s[c] for s in shares) / len(shares) if shares else None) for c, k in enumerate(("error", "checked", "student"))}
    met, conservative = verdict(iv["stays", "error"], cal.alpha, cal.n)
    f = lambda v: "n/a" if v is None else f"{v:.4f}"  # noqa: E731
    ci = lambda k: "n/a" if iv["stays", k] is None else f"{iv['stays', k][0]:.4f} to {iv['stays', k][1]:.4f}"  # noqa: E731
    ratio = lambda a, b: "n/a" if b == 0 else f"{a / b:.4f} ({a}/{b})"  # noqa: E731
    s_on, c_on = alerts["student"][1], alerts["checked"][1]
    if data is not None:
        pair = lambda k: {"lo": None if iv["stays", k] is None else iv["stays", k][0],  # noqa: E731
                          "hi": None if iv["stays", k] is None else iv["stays", k][1]}
        data.update({
            "alpha": cal.alpha, "n_calibration": cal.n, "j": cal.j, "k": cal.k, "q": cal.q, "t": cal.t,
            "binding": cal.binding, "onnx_sha256": cal.onnx_sha256, "n_stays": len(shares),
            "achieved_error": {"mean": mean["error"], **pair("error")},
            "checked_share": {"mean": mean["checked"], **pair("checked")},
            "student_share": {"mean": mean["student"], **pair("student")},
            "verdict": "MET" if met else "NOT MET", "conservative": conservative,
            "bound": float(Fraction(cal.alpha)) + spread(cal.alpha, cal.n), "spread": spread(cal.alpha, cal.n),
            "pooled": {"scored_bins": bins["scored"], "negative_bins": bins["neg"], "above_q": bins["neg_q"],
                       "checked": bins["neg_chk"], "student": bins["neg_t"]},
            "not_sure": {"bins": bins["alert"] - bins["sure"], "of_scored_bins": bins["scored"],
                         "of_student_alerting_bins": bins["alert"]},
            "alerting_bins": {"student": s_on, "checked": c_on},
            "new_alerts": {"student": alerts["student"][0], "checked": alerts["checked"][0]},
            "lost": {"all": dict(lost["all"]), "not_cut_short": dict(lost["uncut"])},
        })
    return [
        f"[{tag}] confidence check, alpha {cal.alpha}, {label}",
        f"calibration facts: n={cal.n} k={cal.k} (j={cal.j}) q={cal.q} t={cal.t} alpha={cal.alpha} "
        f"onnx sha256 {cal.onnx_sha256}",
        f"{'binding' if cal.binding else 'NOT binding (q < t): the check removes no alert'}",
        f"1. achieved error: mean over {len(shares)} held-out non-starting stays of the share of their negative "
        f"scored bins with p > q: {f(mean['error'])}, 95% stay-bootstrap interval {ci('error')}, against alpha {cal.alpha}",
        f"   context: the same share for the checked rule {f(mean['checked'])} ({ci('checked')}), for the student "
        f"alone (p >= t) {f(mean['student'])} ({ci('student')}); pooled over bins: p > q {ratio(bins['neg_q'], bins['neg'])}, "
        f"checked {ratio(bins['neg_chk'], bins['neg'])}, student {ratio(bins['neg_t'], bins['neg'])}",
        f"   verdict: {'MET' if met else 'NOT MET'} (rule: lower end <= alpha + d, d = {spread(cal.alpha, cal.n):.4f}"
        f" from the calibration n){'; conservative (upper end < alpha - d)' if conservative else ''}",
        f"2. not sure (p >= t and p <= q): {ratio(bins['alert'] - bins['sure'], bins['scored'])} of scored bins, "
        f"{ratio(bins['alert'] - bins['sure'], bins['alert'])} of the student's alerting bins",
        f"3. alerting bins: student {s_on}, checked {c_on}, removed {s_on - c_on} "
        f"({'n/a' if s_on == 0 else f'{(s_on - c_on) / s_on:.3f}'}); new alerts: student {alerts['student'][0]}, "
        f"checked {alerts['checked'][0]} (removing bins can split one alert in two, so removed can be negative)",
        *[f"4. episodes ({'all evaluated' if s == 'all' else 'not cut short'}) detected by the student but not by "
          f"the checked rule: alerting in any bin of the window {lost[s]['any']}; caught early (a new alert in the "
          f"window before fully in) {lost[s]['early']}" for s in ("all", "uncut")],
        "   chance and excess over chance for both rows: see the table above",
    ]


def evaluate(folders: Sequence[str], model: Path = MODEL, out: Path = OUT, cal_seeds: range = CAL_SEEDS,
             *, json_path: str | None = None, held_out_set: int = 1) -> str:
    """With `json_path`, the held-out JSON (rules and the confidence blocks) is written next to heldout.txt
    (heldout_2.txt for set 2, which has its own run-once guard)."""
    set2 = held_out_set == 2
    held_output = HELD_OUTPUT_2 if set2 else HELD_OUTPUT
    jobs = _manifests(folders, HELD_SEEDS_2 if set2 else HELD_SEEDS, "held-out set 2" if set2 else "held-out ward-v2")
    if (out / held_output).exists():
        raise TrialError(f"{held_output} exists: the held-out run was already made (delete it only for a counting bug)")
    from . import evaluate as ev
    ev.check_json_path(json_path)  # before any trial is read
    from .student import Scorer, onnx_predict, prob_policy, session
    meta, found = _model(model)
    shape = (jobs[0][2]["days"], jobs[0][2]["speed"], jobs[0][2]["scenario_sha256"])
    cals, policies, notes = {}, [], []
    for tag, m in meta["variants"].items():
        if tag not in found:
            notes.append(f"{tag}: not applicable ({usable(m)}); no rows")
            continue
        cal, digest = load(out / CALIBRATION[tag])
        if cal.seeds != list(cal_seeds):
            raise TrialError(f"{tag}: the calibration was not made on exactly seeds {cal_seeds.start} to {cal_seeds.stop - 1}")
        t = m["thresholds"]["student"]["threshold"]
        verify(cal, variant=tag, onnx_sha256=m["onnx_sha256"], t=t, shape=shape)
        sess = session(found[tag][0])
        scorer = Scorer(lambda x, s=sess: onnx_predict(s, x), m["temperature"], m["student"]["inputs"])
        sfx = "" if tag == "all" else "_no_temp"
        pols = [prob_policy(f"student{sfx}", scorer, t), checked_policy(f"student_checked{sfx}", scorer, t, cal.q),
                prob_policy(f"news2_or_student{sfx}", scorer, t, True),
                checked_policy(f"news2_or_student_checked{sfx}", scorer, t, cal.q, True)]
        policies += pols
        cals[tag] = (cal, digest, scorer, pols)
        notes.append(f"{tag}: calibration {CALIBRATION[tag]} sha256 {digest}")
    result: dict = {}

    def release() -> None:  # per-stay memos must not grow with the number of trials
        Scorer.clear()
        for _, _, sc, _ in cals.values():
            sc._memo.clear()

    text = ev.run([f for _, f, _ in jobs], POLICIES + tuple(policies), result if json_path else None,
                  held_out_set, release)
    files = [f for _, f, _ in jobs]
    Scorer.clear()
    for _, _, scorer, _ in cals.values():
        scorer._memo.clear()
    block = [f"confidence check (slice M3): split-conformal, one negative bin per calibration stay, alpha {ALPHA}", *notes, ""]
    confidence = {}
    label = "held-out set 2, ward-v2-heldout2 seeds 1001-1400" if set2 else "held-out ward-v2 seeds 11-30"
    for tag, (cal, digest, scorer, pols) in cals.items():
        data: dict = {"calibration_sha256": digest}
        block += _block(tag, cal, files, scorer, pols[0], pols[1], data, label) + [f"   calibration file sha256 {digest}", ""]
        confidence[tag] = data
    whole = text + "\n" + "\n".join(block)
    if json_path:
        result["confidence"] = confidence
        ev.write_json(json_path, result)  # first, so a failure writes nothing of heldout.txt
    try:
        with open(out / held_output, "x", encoding="utf-8") as f:  # "x": a second held-out run is refused, not overwritten
            f.write(whole)
    except OSError:
        if json_path:
            os.remove(json_path)
        raise
    return whole


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise TrialError(message)


def _check_ids(run_id: str | None, commit: str | None) -> None:
    if run_id is not None and not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", run_id):
        raise TrialError("--run_id may only hold A-Z a-z 0-9 . _ - and at most 64 characters")
    if commit is not None and not re.fullmatch(r"[\x21-\x7e]{1,128}", commit):
        raise TrialError("--commit must be 1 to 128 printable characters without spaces")


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="research.confidence", add_help=False, allow_abbrev=False)
    sub = p.add_subparsers(dest="command", required=True, parser_class=_Parser)
    for name in ("calibrate", "evaluate"):
        s = sub.add_parser(name, add_help=False, allow_abbrev=False)
        s.add_argument("folders", nargs="*")
        s.add_argument("--model")
        s.add_argument("--out")
        if name == "evaluate":
            s.add_argument("--json")
            s.add_argument("--held-out-set", default="1")
        if name == "calibrate":
            for flag in ("--cache", "--run_log", "--run_id", "--commit"):
                s.add_argument(flag)
            s.add_argument("--workers", type=int)
    return p


def main(argv: list[str]) -> int:
    try:
        a = _parser().parse_args(argv)
        model, out = Path(a.model) if a.model else MODEL, Path(a.out) if a.out else OUT
        if a.command == "calibrate":
            lines = calibrate(a.folders, model, out, a.cache, a.workers, Path(a.run_log) if a.run_log else None,
                              a.run_id, a.commit)
            sys.stdout.write("\n".join(lines) + "\n")
        else:
            from .evaluate import check_set
            held_out_set = check_set(a.held_out_set)
            text = evaluate(a.folders, model, out, json_path=a.json, **({"held_out_set": 2} if held_out_set == 2 else {}))
            name = HELD_OUTPUT_2 if held_out_set == 2 else HELD_OUTPUT
            sys.stdout.write(text + f"\n{name} sha256 {hashlib.sha256(text.encode()).hexdigest()}\n")
    except TrialError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
