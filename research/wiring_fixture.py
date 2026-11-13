"""The golden fixture for the wiring slice (synthetic, research only, not validated).

    python -m research.wiring_fixture --out research/tests/data/wiring/golden.json

It records, bin by bin, what the research code computes for the three models on the toy trial
(`research/tests/data/toy`) and on one long synthetic stay built here: the NEWS2 result, the baseline
state, the student's features, p and check state (variants `all` and `no_temp`), and the reward
policy's z, score and alerting state. The backend cannot import `research/`; its own feature builder,
forward pass, confidence check and policy state machine are tested against this file.

It runs in the ML venv: `research.student` imports MindSpore at module top. p is
`research.plain_student.forward` on the float32-rounded features (the in-process path of the backend).
It holds research values only: the product's extra gates (baseline forming, the 672-bin cap) are NOT
applied. No clock, no path and no host go into the file; the bytes are the same on every run.
"""

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

from biosentry.domain.scoring.tier_a import News2Score
from biosentry.domain.scoring.tier_b.baseline import DEFAULTS, baseline_finding

from . import confidence, plain_student, reward_loop, student
from .confidence import check
from .reward_policy import NAMES, Params, states, z_series
from .rescore import check_parity, score_row
from .trials import BIN_MINUTES, Row, load_trial

ROOT = Path(__file__).parent.parent
TOY = ROOT / "research" / "tests" / "data" / "toy"
DEFAULT_OUT = "research/tests/data/wiring/golden.json"
FORMAT = "biosentry-wiring-golden/1"
STUDENT = "models/tier_c/student_vitals"
CAL = "models/tier_c/confidence_check"
POLICY = "models/tier_b/reward_policy"
VARIANTS = {  # variant -> (has temperature, student json, onnx, calibration, policy)
    "all": (True, "model.json", "model.onnx", "calibration.json", f"{POLICY}/policy.json"),
    "no_temp": (False, "model_no_temp.json", "model_no_temp.onnx", "calibration_no_temp.json",
                f"{POLICY}/no_temperature/policy.json"),
}
ABOUT = (
    "Synthetic data only: the committed toy trial and one stay built in research/wiring_fixture.py. "
    "It is a golden file for the wiring slice: the backend tests its own feature builder, forward pass, "
    "confidence check and policy state machine against the research code, bin by bin. "
    "Each bin's minute is the END of the 15-minute bin, as in the trial format, so the backend's bin_at "
    "start is minute - 15. A bin entry exists for every 15-minute boundary from the first row to the last; "
    "a gap has row null and every other field null. Row values are the stored values of the research Row "
    "(diastolic is not read by NEWS2 and the Row does not hold it). "
    "Features are float32 values written as the Python repr of the float32 converted to float. "
    "p is research.plain_student.forward on those float32 values. check is research.confidence.check(p, t, q) "
    "with 'not sure' written as not_sure. z is the policy's clipped z per vital in the order of constants.policy_vitals. "
    "A NOT RANKED bin has student and policy null (no p, the policy is not alerting and its run is reset). "
    "The long stay has two gaps and two NOT RANKED bins: bins 30 and 45 come while the runs are 0; bins 84 (gap) and "
    "78 (NOT RANKED) come inside the alerting stretch, so the policy goes alerting, reset, not alerting, alerting again. "
    "summary.totals.resets_after_run counts them per variant. "
    "The file holds what the research code computes on every scored bin. It does NOT apply the product's "
    "extra gates (ruling 10: nothing is computed while the baseline forms; ruling 7: the 672-bin cap). "
    "baseline_formed says, per vital, whether baseline_finding returns a finding on the series up to that "
    "bin, so the backend can test its own gate. All toy stays are kept."
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _row(patient: str, b: int, hr, rr, spo2, temp, sbp, bp_minute, consciousness, oxygen) -> Row:
    r = Row(patient, BIN_MINUTES * b, hr, rr, spo2, temp, sbp, bp_minute, consciousness, oxygen, 1, False, None)
    s = score_row(r)
    return replace(r, ranked=isinstance(s, News2Score), news2=s.total if isinstance(s, News2Score) else None)


def synthetic_stay() -> dict[int, Row]:
    """94 bins; bins 1..24 are a quiet baseline, a slow drift from bin 52, two gaps, two NOT RANKED bins, new nurse inputs.

    GAPS: no row. NOT_RANKED: consciousness missing, so NEWS2 abstains. The first of each is early (the runs
    are 0); the second of each is inside the alerting stretch, so the policy's run reset is tested: alerting,
    reset, not alerting for p - 1 scored bins, alerting again. CUFF1, CUFF2: a new blood pressure with its own
    bp_minute. OXYGEN: oxygen switched on. All values are physically possible.
    """
    gaps, not_ranked, cuff1, cuff2, oxygen, drift = (30, 84), (45, 78), 54, 74, 72, 52
    rows: dict[int, Row] = {}
    for b in range(1, 95):
        if b in gaps:
            continue
        wob = ((b * 7) % 5) - 2
        d = min(1.0, max(0.0, (b - drift) / 32)) ** 1.5  # a slow start, full at bin 84, held to bin 92, then recovers
        if b > 92:
            d = max(0.0, 1.0 - (b - 92) / 2)  # recovers in the last bins
        hr = round(72 + wob + 52 * d, 1)
        rr = round(16 + (b % 2) + 9 * d, 1)
        spo2 = round(97 - (b % 2) * 0.5 - 6 * d, 1)
        temp = round(36.8 + wob * 0.05 + 1.5 * d, 2)
        sbp, bpm = (120.0, 5.0) if b < cuff1 else (108.0, 15.0 * cuff1 - 5.0) if b < cuff2 else (98.0, 15.0 * cuff2 - 5.0)
        rows[15 * b] = _row("synthetic", b, hr, rr, spo2, temp, sbp, bpm,
                            None if b in not_ranked else "A", b >= oxygen)
    return rows


def _fl(x) -> float:
    return float(np.float32(x))


def _row_json(r: Row) -> dict:
    return {k: getattr(r, k) for k in ("heart_rate", "respiratory_rate", "spo2", "temperature", "systolic")} | {
        "diastolic": None, "bp_minute": r.bp_minute, "consciousness": r.consciousness,
        "on_oxygen": r.on_oxygen, "spo2_scale": r.spo2_scale}


def stay_json(sid: str, source: str, admit: int, rows: dict[int, Row], models: dict) -> dict:
    minutes_all = list(range(min(rows), max(rows) + 1, BIN_MINUTES))
    internal_minutes, internal = student.stay_features(rows)
    at = {m: i for i, m in enumerate(internal_minutes)}
    zmap = dict(z_series(rows))
    series: dict[str, list] = {v: [] for v in NAMES}
    out_bins = []
    run_states = {v: states(models[v]["params"], z_series(rows), models[v]["form"]) for v in VARIANTS}
    for m in minutes_all:
        r = rows.get(m)
        for v in NAMES:
            series[v].append(None if r is None else getattr(r, v))
        if r is None:
            out_bins.append({"minute": m, "row": None, "news2": None, "baseline_formed": None, "student": None,
                             "policy": None, "policy_run_reset": None})
            continue
        s = score_row(r)
        ranked = isinstance(s, News2Score)
        tp = sum(p.score for p in s.parameters if p.parameter == "temperature") if ranked else None
        b = {"minute": m, "row": _row_json(r),
             "news2": {"ranked": ranked, "total": s.total if ranked else None, "temperature_points": tp},
             "baseline_formed": {v: isinstance(baseline_finding(v, series[v], DEFAULTS), tuple) for v in NAMES},
             "student": None, "policy": None, "policy_run_reset": not ranked}
        if ranked:
            b["student"], b["policy"] = {}, {}
            for v, (temp, *_r) in VARIANTS.items():
                mod = models[v]
                feats = [_fl(x) for x in student.view(internal[at[m]][None, :].astype(np.float32), temp, "trend")[0]]
                p = plain_student.forward(mod["plain"], feats)
                b["student"][v] = {"features": feats, "p": p, "check": check(p, mod["t"], mod["q"]).replace(" ", "_")}
                z = zmap[m]
                w = mod["params"].weights
                sc = sum(wi * zi for wi, zi in zip(w, z)) if mod["form"] == "sum" else max(wi * zi for wi, zi in zip(w, z))
                b["policy"][v] = {"z": list(z), "score": sc, "alerting": run_states[v][m]}
        out_bins.append(b)
    return {"id": sid, "source": source, "admit_minute": admit, "bins": out_bins}


def summary(stays: list[dict], models: dict) -> dict:
    """Counts. resets_after_run: gaps and NOT RANKED bins that come right after a bin whose run is above 0, and
    suppressed_alerts: scored bins where a counter that is never reset by them would alert and the policy does not."""
    per, totals = [], {"check": {v: {"alert": 0, "not_sure": 0, "quiet": 0} for v in VARIANTS},
                       "policy_alerting": {v: 0 for v in VARIANTS},
                       "resets_after_run": {v: {"gap": 0, "not_ranked": 0, "suppressed_alerts": 0} for v in VARIANTS}}
    for st in stays:
        bins = st["bins"]
        scored = [b for b in bins if b["student"] is not None]
        per.append({"id": st["id"], "bins": len(bins), "rows": sum(b["row"] is not None for b in bins),
                    "gaps": sum(b["row"] is None for b in bins),
                    "not_ranked": sum(b["row"] is not None and not b["news2"]["ranked"] for b in bins),
                    "scored": len(scored)})
        for b in scored:
            for v in VARIANTS:
                totals["check"][v][b["student"][v]["check"]] += 1
                totals["policy_alerting"][v] += b["policy"][v]["alerting"]
    for v in VARIANTS:
        m, c = models[v], totals["resets_after_run"][v]
        for st in stays:
            run = free = 0  # run: reset by a gap or NOT RANKED bin; free: never reset by them
            for b in st["bins"]:
                if b["policy"] is None:
                    c["gap" if b["row"] is None else "not_ranked"] += run > 0
                    run = 0
                    continue
                e = b["policy"][v]
                hit = e["score"] >= m["params"].theta
                run, free = (run + 1, free + 1) if hit else (0, 0)
                c["suppressed_alerts"] += free >= m["params"].p and not e["alerting"]
    scored_n = sum(p["scored"] for p in per)
    totals["scored_bins"] = scored_n
    totals["policy_not_alerting"] = {v: scored_n - totals["policy_alerting"][v] for v in VARIANTS}
    return {"per_stay": per, "totals": totals}


def build() -> dict:
    files, models = [], {}
    for v, (temp, js, onnx, cal_name, pol) in VARIANTS.items():
        paths = [f"{STUDENT}/{js}", f"{STUDENT}/{onnx}", f"{CAL}/{cal_name}", pol]
        for p in paths:
            data = (ROOT / p).read_bytes()
            files.append({"path": p, "bytes": len(data), "sha256": _sha(data)})
        plain = plain_student.load(ROOT / paths[0])
        cal, _ = confidence.load(ROOT / paths[2])
        confidence.verify(cal, variant=v, onnx_sha256=_sha((ROOT / paths[1]).read_bytes()), t=plain.threshold,
                          shape=student.SHAPE)
        obj, _ = reward_loop.load_policy(ROOT / pol, temp)
        models[v] = {"plain": plain, "t": cal.t, "q": cal.q, "params": Params.from_dict(obj["params"]),
                     "form": obj["config"]["form"]}
    trial = load_trial(TOY)
    check_parity(trial)
    by: dict[str, dict[int, Row]] = {e.patient: {} for e in trial.episodes}
    for r in trial.rows:
        by[r.patient][r.minute] = r
    stays = [stay_json(e.patient, "toy trial research/tests/data/toy", e.admit_minute, by[e.patient], models)
             for e in trial.episodes if by[e.patient]]
    stays.append(stay_json("synthetic-long", "synthetic long stay", 0, synthetic_stay(), models))
    return {
        "format": FORMAT, "about": ABOUT, "bin_minutes": BIN_MINUTES,
        "files": sorted(files, key=lambda f: f["path"]),
        "constants": {
            "alpha": confidence.ALPHA,
            "variants": {v: {"t": m["t"], "q": m["q"], "policy_params": m["params"].as_dict(),
                             "policy_form": m["form"]} for v, m in models.items()},
            "policy_vitals": list(NAMES),
            "baseline": {"forming_bins": DEFAULTS.window_bins, "window_bins": DEFAULTS.window_bins,
                         "floors": dict(DEFAULTS.floors), "mad_to_sd": 1.4826},
            "lag_bins": {"1h": student.LAG_1H, "4h": student.LAG_4H},
        },
        "feature_names": {v: student.feature_names(VARIANTS[v][0], "trend") for v in VARIANTS},
        "stays": stays, "summary": summary(stays, models),
    }


def dump(obj: dict) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=1, allow_nan=False) + "\n").encode()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="research.wiring_fixture", description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=DEFAULT_OUT)
    out = Path(ap.parse_args(argv).out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(dump(build()))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
