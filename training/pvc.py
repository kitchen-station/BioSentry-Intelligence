#!/usr/bin/env python
"""Premature-ventricular-beat metric code and the RR-ratio comparator (M3 exit gate, item 1). Dev tool.

DEVELOPMENT ONLY. This file reads the 22 MIT-BIH Arrhythmia development records (rr_baseline.DS1)
and no others: it names no other record, so nothing computed here can come from the test split.
That split is scored once, by its own command and PR, after a pin commit has frozen the method.

The pieces, all numpy and all unit-tested without a recording:

  - beat context: the intervals before and after each detected beat and the window's median
    interval; a beat at a window edge has no context and is never called (windows stay independent,
    as in the product);
  - the primary comparator, an RR-ratio rule (interval before the beat / window median < theta),
    and a secondary variant that also wants a compensatory pause (interval after / median > phi);
  - beat-level scoring against annotated beats, one-to-one within 150 ms. Positive = annotated V;
    every other beat symbol is a negative and stays in the denominators;
  - sensitivity, PPV over every detection called PVC (matched or not), specificity over annotated
    non-V beats, pooled over records, record-level bootstrap, paired differences;
  - beats lost to window placement: V beats missed on the evaluated window grid that are found when
    the grid is shifted by half a window.

    python pvc.py develop --data DATA_DIR --out OUT_DIR
"""

import argparse
import json
import pathlib
import sys
from collections.abc import Sequence
from typing import Any, NamedTuple

import numpy as np

try:
    import rr_baseline as rb
except ModuleNotFoundError:  # `python -I pvc.py`: the script's own directory is not on the path
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import rr_baseline as rb

FS = rb.FS
WINDOW = rb.WINDOW
MIN_CONTEXT_BEATS = 4  # fewer detections in a window: no median interval, no beat is called
MATCH_TOL_S = rb.MATCH_TOL_S
POSITIVE = "V"
FRACTIONS = (0.05, 0.10, 0.15, 0.20, 0.25)  # detector threshold fractions tried on DS1
MIN_BEAT_PPV = 0.99  # a detector fraction must keep this beat-level PPV on DS1 ...
DETECTOR_FRACTION = 0.15  # ... and, of those, gives the most V beats found (see `develop`)
THETAS = tuple(round(0.50 + 0.01 * k, 2) for k in range(46))  # 0.50 ... 0.95
PHIS = tuple(round(1.00 + 0.05 * k, 2) for k in range(9))  # 1.00 ... 1.40


# --- beat context and the rules -----------------------------------------------------------------


class Detections(NamedTuple):
    """Beats found on a grid of independent windows, with their context in the window."""

    pos: np.ndarray  # sample index into the record
    rr_prev: np.ndarray  # seconds, NaN for the first beat of a window
    rr_next: np.ndarray  # seconds, NaN for the last beat of a window
    median_rr: np.ndarray  # the window's median interval, NaN with < MIN_CONTEXT_BEATS beats
    window: np.ndarray  # window index on the grid
    covered: tuple[int, int]  # [first, last) sample covered by whole windows


def window_context(peaks: np.ndarray, fs: float = FS) -> tuple[np.ndarray, np.ndarray, float]:
    """Interval before and after each beat of one window, and the window's median interval."""
    n = peaks.size
    before = np.full(n, np.nan)
    after = np.full(n, np.nan)
    rr = np.diff(peaks.astype(np.float64)) / fs
    before[1:] = rr
    after[:-1] = rr
    median = float(np.median(rr)) if n >= MIN_CONTEXT_BEATS else float("nan")
    return before, after, median


def detect_grid(
    x: np.ndarray,
    start: int = 0,
    window: int = WINDOW,
    fs: float = FS,
    fraction: float = DETECTOR_FRACTION,
) -> Detections:
    """Detect in consecutive independent windows from `start`; a trailing partial window is dropped."""
    pos: list[np.ndarray] = []
    prev: list[np.ndarray] = []
    nxt: list[np.ndarray] = []
    med: list[np.ndarray] = []
    win: list[np.ndarray] = []
    starts = range(start, len(x) - window + 1, window)
    for k, s0 in enumerate(starts):
        peaks = rb.detect_r_peaks(x[s0 : s0 + window], fs, fraction)
        before, after, median = window_context(peaks, fs)
        pos.append(peaks + s0)
        prev.append(before)
        nxt.append(after)
        med.append(np.full(peaks.size, median))
        win.append(np.full(peaks.size, k, dtype=np.int64))
    end = start + len(starts) * window
    empty_f, empty_i = np.zeros(0), np.zeros(0, dtype=np.int64)

    def cat(parts: list[np.ndarray], empty: np.ndarray) -> np.ndarray:
        return np.concatenate(parts) if parts else empty

    return Detections(
        cat(pos, empty_i),
        cat(prev, empty_f),
        cat(nxt, empty_f),
        cat(med, empty_f),
        cat(win, empty_i),
        (start, end),
    )


def rr_ratio_rule(rr_prev: np.ndarray, median_rr: np.ndarray, theta: float) -> np.ndarray:
    """Premature: the interval before the beat is shorter than theta times the window's median.
    A beat without a context is never called."""
    with np.errstate(invalid="ignore"):
        return np.asarray(rr_prev < theta * median_rr)


def rr_pause_rule(
    rr_prev: np.ndarray, rr_next: np.ndarray, median_rr: np.ndarray, theta: float, phi: float
) -> np.ndarray:
    """Premature and followed by a pause longer than phi times the median interval."""
    with np.errstate(invalid="ignore"):
        return rr_ratio_rule(rr_prev, median_rr, theta) & np.asarray(rr_next > phi * median_rr)


# --- matching and scoring ----------------------------------------------------------------------


def match_indices(
    reference: np.ndarray, detected: np.ndarray, tolerance: float
) -> tuple[np.ndarray, np.ndarray]:
    """One-to-one matching of two sorted position arrays within `tolerance` (inclusive): for each
    reference the index of its detection, and for each detection the index of its reference, -1
    where there is none. Same pairing as rr_baseline.match_beats."""
    to_det = np.full(len(reference), -1, dtype=np.int64)
    to_ref = np.full(len(detected), -1, dtype=np.int64)
    i = j = 0
    while i < len(reference) and j < len(detected):
        gap = detected[j] - reference[i]
        if gap < -tolerance:
            j += 1
        elif gap > tolerance:
            i += 1
        else:
            to_det[i] = j
            to_ref[j] = i
            i += 1
            j += 1
    return to_det, to_ref


class BeatTable(NamedTuple):
    """One record scored against its annotated beats (only beats inside the evaluated windows)."""

    ref_pos: np.ndarray
    ref_symbol: np.ndarray
    det_to_ref: np.ndarray  # reference index matched to each detection, -1 if none
    detections: Detections


def build_table(
    ref_pos: np.ndarray, ref_symbol: np.ndarray, det: Detections, tolerance_s: float = MATCH_TOL_S
) -> BeatTable:
    lo, hi = det.covered
    inside = (ref_pos >= lo) & (ref_pos < hi)
    order = np.argsort(ref_pos[inside], kind="stable")
    pos = np.asarray(ref_pos[inside])[order]
    sym = np.asarray(ref_symbol[inside])[order]
    _, to_ref = match_indices(pos, det.pos.astype(np.float64), tolerance_s * FS)
    return BeatTable(pos, sym, to_ref, det)


def score_called(table: BeatTable, called: np.ndarray) -> dict[str, Any]:
    """Counts for one record and one set of calls (one boolean per detection).

    n_v = tp + fn_called_normal + fn_missed, where fn_missed is a V beat no detection reached."""
    is_v = table.ref_symbol == POSITIVE
    matched = table.det_to_ref >= 0
    ref_of = np.where(matched, table.det_to_ref, 0)
    det_is_v = matched & is_v[ref_of] if len(is_v) else np.zeros(len(matched), dtype=bool)
    reached = np.zeros(len(is_v), dtype=bool)
    reached[table.det_to_ref[matched]] = True
    wrong = called & matched & ~det_is_v
    by_symbol: dict[str, int] = {}
    for symbol in table.ref_symbol[table.det_to_ref[wrong]]:
        by_symbol[str(symbol)] = by_symbol.get(str(symbol), 0) + 1
    n_v = int(is_v.sum())
    return {
        "n_v": n_v,
        "n_nonv": int(len(is_v) - n_v),
        "tp": int((called & det_is_v).sum()),
        "fn_missed": int((is_v & ~reached).sum()),
        "fn_called_normal": int((det_is_v & ~called).sum()),
        "fp_matched": int(wrong.sum()),
        "fp_spurious": int((called & ~matched).sum()),
        "fp_by_symbol": by_symbol,
        "detections": int(len(called)),
        "called": int(called.sum()),
    }


def placement_misses(table: BeatTable, shifted: Detections, tolerance_s: float = MATCH_TOL_S) -> int:
    """V beats with no detection on the evaluated grid but one on the shifted grid: beats lost to
    where the window edges fell. Only beats the shifted grid also covers can be recovered."""
    is_v = table.ref_symbol == POSITIVE
    found = np.zeros(len(is_v), dtype=bool)
    found[table.det_to_ref[table.det_to_ref >= 0]] = True
    lo, hi = shifted.covered
    reachable = (table.ref_pos >= lo) & (table.ref_pos < hi)
    candidates = np.flatnonzero(is_v & ~found & reachable)
    to_det, _ = match_indices(
        np.sort(table.ref_pos[candidates]), shifted.pos.astype(np.float64), tolerance_s * FS
    )
    return int((to_det >= 0).sum())


# --- rates, F1 and the bootstrap statistics --------------------------------------------------


def f1(tp: int, fp: int, fn: int) -> float:
    return 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else float("nan")


def detection_quality(tables: Sequence[BeatTable]) -> dict[str, float]:
    """Beat-level PPV of the detections and the share of V beats some detection reached."""
    matched = sum(int((t.det_to_ref >= 0).sum()) for t in tables)
    detected = sum(len(t.det_to_ref) for t in tables)
    v_ref = sum(int((t.ref_symbol == POSITIVE).sum()) for t in tables)
    v_found = 0
    for t in tables:
        reached = np.zeros(len(t.ref_symbol), dtype=bool)
        reached[t.det_to_ref[t.det_to_ref >= 0]] = True
        v_found += int((reached & (t.ref_symbol == POSITIVE)).sum())
    return {
        "beat_ppv": matched / detected if detected else float("nan"),
        "v_found": v_found / v_ref if v_ref else float("nan"),
    }


def choose_fraction(
    quality: dict[float, dict[str, float]], min_ppv: float = MIN_BEAT_PPV
) -> float:
    """The fraction that finds the most V beats among those keeping the beat-level PPV at or above
    `min_ppv`; a tie goes to the larger fraction (fewer false detections)."""
    allowed = [f for f in quality if quality[f]["beat_ppv"] >= min_ppv]
    if not allowed:
        raise ValueError("no detector fraction keeps the required beat-level PPV")
    return max(allowed, key=lambda f: (quality[f]["v_found"], f))


def first_best(scores: dict[Any, float]) -> Any:
    """The key with the highest score; a tie goes to the first key in the dictionary's order."""
    best = None
    for key, value in scores.items():
        if best is None or value > scores[best]:
            best = key
    return best


def pooled(records: Sequence[dict[str, Any]], method: str) -> dict[str, float]:
    """Pooled (micro) sensitivity, PPV and specificity of one method over records."""
    n_v = sum(r["n_v"] for r in records)
    n_nonv = sum(r["n_nonv"] for r in records)
    tp = sum(r["methods"][method]["tp"] for r in records)
    fp_m = sum(r["methods"][method]["fp_matched"] for r in records)
    fp_s = sum(r["methods"][method]["fp_spurious"] for r in records)

    def ratio(a: float, b: float) -> float:
        return a / b if b else float("nan")

    return {
        "sensitivity": ratio(tp, n_v),
        "ppv": ratio(tp, tp + fp_m + fp_s),
        "specificity": 1.0 - ratio(fp_m, n_nonv) if n_nonv else float("nan"),
        "f1": f1(tp, fp_m + fp_s, n_v - tp),
    }


def pvc_statistics(
    records: Sequence[dict[str, Any]], methods: Sequence[str], pairs: Sequence[tuple[str, str]]
) -> dict[str, float]:
    """Everything the report bootstraps: each method's pooled rates and paired differences."""
    out: dict[str, float] = {}
    rates = {m: pooled(records, m) for m in methods}
    for m, r in rates.items():
        for key in ("sensitivity", "ppv", "specificity"):
            out[f"{m}_{key}"] = r[key]
    for a, b in pairs:
        for key in ("sensitivity", "ppv", "specificity"):
            out[f"delta_{a}_minus_{b}_{key}"] = rates[a][key] - rates[b][key]
    return out


# --- development on DS1 ------------------------------------------------------------------------


def mitdb_beats(root: pathlib.Path, name: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """MLII by name at 250 Hz, annotated beat positions (250 Hz samples) and their symbols."""
    import wfdb

    rh = rb._rhythm_head()
    rec = wfdb.rdrecord(str(root / name))
    x = rh.resample(rec.p_signal[:, rec.sig_name.index("MLII")].astype(np.float32), int(rec.fs))
    ann = wfdb.rdann(str(root / name), "atr")
    keep = np.array([s in rb.BEAT_SYMBOLS for s in ann.symbol], dtype=bool)
    pos = np.asarray(ann.sample)[keep] * (FS / int(rec.fs))
    return x, pos, np.asarray(ann.symbol)[keep]


def cmd_develop(args: argparse.Namespace) -> None:
    """On DS1 only: choose the detector's threshold fraction, then the comparator's parameters by
    pooled F1, and report DS1 (development) numbers. Nothing here is a test result."""
    root = pathlib.Path(args.data) / "mitdb" / "extracted"
    data = {name: mitdb_beats(root, name) for name in rb.DS1}

    def tables_for(fraction: float, start: int = 0) -> dict[str, BeatTable]:
        return {
            n: build_table(pos, sym, detect_grid(x, start, fraction=fraction))
            for n, (x, pos, sym) in data.items()
        }

    detection = {f: detection_quality(list(tables_for(f).values())) for f in FRACTIONS}
    fraction = choose_fraction(detection)
    tables = tables_for(fraction)
    shifted = tables_for(fraction, start=WINDOW // 2)

    def rows(theta: float, phi: float | None) -> list[dict[str, Any]]:
        out = []
        for name, t in tables.items():
            d = t.detections
            if phi is None:
                called = rr_ratio_rule(d.rr_prev, d.median_rr, theta)
            else:
                called = rr_pause_rule(d.rr_prev, d.rr_next, d.median_rr, theta, phi)
            c = score_called(t, called)
            out.append({"name": name, "n_v": c["n_v"], "n_nonv": c["n_nonv"], "methods": {"m": c}})
        return out

    f1_primary = {th: pooled(rows(th, None), "m")["f1"] for th in THETAS}
    theta = first_best(f1_primary)
    f1_pair = {(th, ph): pooled(rows(th, ph), "m")["f1"] for th in THETAS for ph in PHIS}
    theta2, phi = first_best(f1_pair)
    best = rows(theta, None)
    res = {
        "records": list(rb.DS1),
        "detector": {
            "criterion": f"most V beats found with beat-level PPV >= {MIN_BEAT_PPV}, DS1 only",
            "by_fraction": {str(f): v for f, v in detection.items()},
            "chosen_fraction": fraction,
        },
        "comparator_selection": "pooled beat-level F1 over the development records only",
        "primary_rr_ratio_rule": {"theta": theta, **pooled(best, "m")},
        "secondary_pause_rule": {"theta": theta2, "phi": phi, **pooled(rows(theta2, phi), "m")},
        "f1_by_theta": f1_primary,
        "v_reference": sum(c["n_v"] for c in best),
        "v_missed_by_detector": sum(c["methods"]["m"]["fn_missed"] for c in best),
        "v_lost_to_window_placement": sum(placement_misses(tables[n], shifted[n].detections) for n in tables),
        "fp_by_symbol": _merge_counts(c["methods"]["m"]["fp_by_symbol"] for c in best),
    }
    text = json.dumps(res, indent=1)
    pathlib.Path(args.out).mkdir(parents=True, exist_ok=True)
    (pathlib.Path(args.out) / "pvc_rule_ds1.json").write_text(text + "\n")
    print(text)


def _merge_counts(parts: Any) -> dict[str, int]:
    total: dict[str, int] = {}
    for part in parts:
        for key, value in part.items():
            total[key] = total.get(key, 0) + value
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("develop")
    p.add_argument("--data", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_develop)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
