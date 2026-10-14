#!/usr/bin/env python
"""A small learned head for premature ventricular beats (M3 exit gate, item 1). Dev tool.

DEVELOPMENT ONLY, like pvc.py: it reads the 22 MIT-BIH Arrhythmia development records (DS1) and
names no other record. The model is fitted on DS1; the threshold is chosen from out-of-fold
predictions of a record-grouped cross-validation on DS1 (no beat-level split anywhere, early
stopping off, no record in two folds). Nothing is scored on the test split here: that happens
once, in its own PR, after the pin.

What it is. Each beat found by the detector (rr_baseline.detect_r_peaks on independent 10 s
windows, threshold fraction chosen on DS1 in pvc.py) gets twelve numbers: the intervals before
and after it relative to the window's median interval (with presence flags), and how it differs
from the window's own median beat (correlation, amplitude and width ratios, polarity) plus its
absolute width and amplitude. Sixty shallow boosted trees turn them into one probability. The
trees are exported as arrays and evaluated with numpy alone.

A raw-snippet 1-D CNN was tried first, on the same folds: it called every bundle-branch-block beat
a PVC (out-of-fold F1 0.35, and 0.51 with a window-template channel and batch norm). With 22
development patients the beat-relative features generalise across records and the CNN did not.

A beat whose snippet would leave its window is not callable (windows stay independent, as in the
product): it is never called PVC, and a V beat among them is a false negative.

    python pvc_head.py train --data DATA_DIR --out OUT_DIR --models-dir ../models/tier_b/ectopy
"""

import argparse
import hashlib
import json
import pathlib
import sys
from typing import Any, NamedTuple

import numpy as np

try:
    import pvc
    import rr_baseline as rb
except ModuleNotFoundError:  # `python -I pvc_head.py`: the script's directory is not on the path
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import pvc
    import rr_baseline as rb

FS = rb.FS
WINDOW = rb.WINDOW
HALF = 60  # samples either side of the R peak: 0.24 s
SNIPPET = 2 * HALF
CLIP = 10.0
MAD_SCALE = 1.4826
EPS = 1e-6
CONTEXT_CLIP = (-1.0, 2.0)  # (interval / window median) - 1, clipped
MIN_TEMPLATE_BEATS = 4  # callable beats a window needs before it has a median beat
FEATURE_NAMES = (
    "has_prev",
    "ratio_prev",
    "has_next",
    "ratio_next",
    "has_template",
    "template_corr",
    "log2_amplitude_ratio",
    "log2_width_ratio",
    "polarity_agreement",
    "width_25",
    "peak_to_peak",
    "max_abs",
)
N_FEATURES = len(FEATURE_NAMES)
WIDTH_FRACTION = 0.25  # a beat's width: samples above this share of its largest deflection
PEAK_TO_PEAK_CAP = 40.0

# the boosted trees: fixed before any fold was fitted; early stopping is off because its
# validation split is a random beat-level one (the Evaluator's record-grouping condition)
GBM = {
    "max_depth": 3,
    "max_iter": 60,
    "learning_rate": 0.1,
    "min_samples_leaf": 50,
    "l2_regularization": 1.0,
    "early_stopping": False,
    "random_state": 0,
}
FOLDS = 5
THRESHOLDS = tuple(round(0.05 * k, 2) for k in range(1, 20))
SEED = rb.SEED
N_GOLDEN = 8
TREE_ARRAYS = ("tree_feature", "tree_threshold", "tree_left", "tree_right", "tree_value")


# --- signal preprocessing (numpy; the same recipe as the arrhythmia head) ------------------------


def sos_filter(x: np.ndarray, sos: np.ndarray) -> np.ndarray:
    """Causal second-order-section filter (direct form II transposed) along the last axis,
    from zero state, vectorised over the leading axis."""
    y = np.asarray(x, dtype=np.float64).copy()
    for b0, b1, b2, _a0, a1, a2 in np.asarray(sos, dtype=np.float64):
        z1 = np.zeros(y.shape[0])
        z2 = np.zeros(y.shape[0])
        out = np.empty_like(y)
        for t in range(y.shape[1]):
            xi = y[:, t]
            yi = b0 * xi + z1
            z1 = b1 * xi - a1 * yi + z2
            z2 = b2 * xi - a2 * yi
            out[:, t] = yi
        y = out
    return y


def preprocess_windows(raw: np.ndarray, sos: np.ndarray) -> np.ndarray:
    """Band-pass each window on its own from zero state, then median/MAD normalise and clip."""
    filtered = sos_filter(raw, sos).astype(np.float32)
    med = np.median(filtered, axis=1, keepdims=True)
    mad = np.median(np.abs(filtered - med), axis=1, keepdims=True) * np.float32(MAD_SCALE)
    scaled = (filtered - med) / (mad + np.float32(EPS))
    return np.asarray(np.clip(scaled, -CLIP, CLIP), dtype=np.float32)


def callable_mask(pos_in_window: np.ndarray) -> np.ndarray:
    """A beat can be called when its whole snippet lies inside its own window."""
    return np.asarray((pos_in_window >= HALF) & (pos_in_window + HALF <= WINDOW))


def extract_snippets(
    normalised: np.ndarray, window: np.ndarray, pos_in_window: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """(n, SNIPPET) snippets and the callable mask; rows that are not callable are zeros."""
    ok = callable_mask(pos_in_window)
    out = np.zeros((len(window), SNIPPET), dtype=np.float32)
    for i in np.flatnonzero(ok):
        p = int(pos_in_window[i])
        out[i] = normalised[window[i], p - HALF : p + HALF]
    return out, ok


def context_features(rr_prev: np.ndarray, rr_next: np.ndarray, median_rr: np.ndarray) -> np.ndarray:
    """[has_prev, has_next, ratio_prev - 1, ratio_next - 1] with the ratios zero where absent."""
    with np.errstate(invalid="ignore", divide="ignore"):
        prev = rr_prev / median_rr
        nxt = rr_next / median_rr
    has_prev = np.isfinite(prev)
    has_next = np.isfinite(nxt)
    lo, hi = CONTEXT_CLIP
    out = np.zeros((len(rr_prev), 4), dtype=np.float32)
    out[:, 0] = has_prev
    out[:, 1] = has_next
    out[has_prev, 2] = np.clip(prev[has_prev] - 1.0, lo, hi)
    out[has_next, 3] = np.clip(nxt[has_next] - 1.0, lo, hi)
    return out


# --- the twelve numbers ------------------------------------------------------------------------


def _width(s: np.ndarray) -> float:
    a = np.abs(s)
    top = float(a.max())
    return float((a >= WIDTH_FRACTION * top).sum()) if top > 0 else 0.0


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    if float(a.std()) == 0.0 or float(b.std()) == 0.0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _polarity(s: np.ndarray) -> float:
    return float(np.sign(s[int(np.abs(s).argmax())]))


def beat_features(
    snippets: np.ndarray, callable_: np.ndarray, window: np.ndarray, context: np.ndarray
) -> np.ndarray:
    """(n, N_FEATURES) float64 rows. A beat that is not callable keeps zeros everywhere except the
    two interval ratios, which read 1.0 beside their presence flags."""
    n = len(window)
    out = np.zeros((n, N_FEATURES), dtype=np.float64)
    out[:, 0] = context[:, 0]
    out[:, 1] = np.where(context[:, 0] > 0, context[:, 2] + 1.0, 1.0)
    out[:, 2] = context[:, 1]
    out[:, 3] = np.where(context[:, 1] > 0, context[:, 3] + 1.0, 1.0)
    snips = np.asarray(snippets, dtype=np.float64)
    for w in np.unique(window):
        idx = np.flatnonzero((window == w) & callable_)
        if len(idx) < MIN_TEMPLATE_BEATS:
            continue
        template = np.median(snips[idx], axis=0)
        t_pp = float(template.max() - template.min())
        t_width = _width(template)
        t_sign = _polarity(template)
        for i in idx:
            s = snips[i]
            out[i, 4] = 1.0
            out[i, 5] = _corr(s, template)
            out[i, 6] = np.log2((float(s.max() - s.min()) + 1e-3) / (t_pp + 1e-3))
            out[i, 7] = np.log2((_width(s) + 1.0) / (t_width + 1.0))
            out[i, 8] = _polarity(s) * t_sign
    for i in np.flatnonzero(callable_):
        s = snips[i]
        out[i, 9] = _width(s) / SNIPPET
        out[i, 10] = min(float(s.max() - s.min()), PEAK_TO_PEAK_CAP)
        out[i, 11] = float(np.abs(s).max())
    return out


# --- the boosted trees (numpy forward) ------------------------------------------------------------


def trees_raw(params: dict[str, np.ndarray], features: np.ndarray) -> np.ndarray:
    """Sum of the leaf values reached in every tree, plus the baseline: the model's raw score."""
    feat = params["tree_feature"]
    thr = params["tree_threshold"]
    left = params["tree_left"]
    right = params["tree_right"]
    val = params["tree_value"]
    x = np.asarray(features, dtype=np.float64)
    n, n_trees = x.shape[0], feat.shape[0]
    node = np.zeros((n, n_trees), dtype=np.int64)
    tree = np.arange(n_trees)[None, :]
    row = np.arange(n)[:, None]
    for _ in range(int(params["max_depth"][0])):  # one step per level reaches every leaf
        f = feat[tree, node].astype(np.int64)
        is_leaf = f < 0
        go_left = x[row, np.maximum(f, 0)] <= thr[tree, node]
        step = np.where(go_left, left[tree, node], right[tree, node]).astype(np.int64)
        node = np.where(is_leaf, node, step)
    return np.asarray(val[tree, node].sum(axis=1) + float(params["baseline"][0]))


def sigmoid(z: np.ndarray) -> np.ndarray:
    """Logistic function that does not overflow for large |z|."""
    v = np.asarray(z, dtype=np.float64)
    e = np.exp(-np.abs(v))
    return np.asarray(np.where(v >= 0, 1.0 / (1.0 + e), e / (1.0 + e)))


def predict_proba(params: dict[str, np.ndarray], features: np.ndarray) -> np.ndarray:
    return sigmoid(trees_raw(params, features))


def load_params(path: pathlib.Path) -> dict[str, np.ndarray]:
    with np.load(path, allow_pickle=False) as data:
        return {k: data[k] for k in data.files}


def n_nodes(params: dict[str, np.ndarray]) -> int:
    """Nodes (splits and leaves) reachable in the ensemble, padding excluded: a tree is stored from
    its root and every node a split points at belongs to it."""
    feat = params["tree_feature"]
    count = 0
    for t in range(feat.shape[0]):
        seen = {0}
        stack = [0]
        while stack:
            node = stack.pop()
            if feat[t, node] >= 0:
                for child in (int(params["tree_left"][t, node]), int(params["tree_right"][t, node])):
                    if child not in seen:
                        seen.add(child)
                        stack.append(child)
        count += len(seen)
    return count


# --- one record, beat by beat ----------------------------------------------------------------


class RecordBeats(NamedTuple):
    table: Any  # pvc.BeatTable
    features: np.ndarray
    callable: np.ndarray
    label: np.ndarray  # True where the detection is matched to an annotated V beat


def record_beats(
    x: np.ndarray, ref_pos: np.ndarray, ref_symbol: np.ndarray, sos: np.ndarray
) -> RecordBeats:
    det = pvc.detect_grid(x, fraction=pvc.DETECTOR_FRACTION)
    table = pvc.build_table(ref_pos, ref_symbol, det)
    n_windows = (det.covered[1] - det.covered[0]) // WINDOW
    raw = x[: n_windows * WINDOW].reshape(n_windows, WINDOW)
    normalised = preprocess_windows(raw, sos) if n_windows else np.zeros((0, WINDOW), np.float32)
    pos_in = det.pos - det.window * WINDOW
    snippets, ok = extract_snippets(normalised, det.window, pos_in)
    matched = table.det_to_ref >= 0
    label = np.zeros(len(det.pos), dtype=bool)
    label[matched] = table.ref_symbol[table.det_to_ref[matched]] == pvc.POSITIVE
    context = context_features(det.rr_prev, det.rr_next, det.median_rr)
    return RecordBeats(table, beat_features(snippets, ok, det.window, context), ok, label)


def apply_threshold(prob: np.ndarray, callable_: np.ndarray, threshold: float) -> np.ndarray:
    """A beat is called when it is callable and its probability reaches the threshold (inclusive)."""
    called = np.zeros(len(prob), dtype=bool)
    called[callable_] = prob[callable_] >= threshold
    return called


def call_record(
    params: dict[str, np.ndarray], beats: RecordBeats, threshold: float | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """(probability per detection, called per detection); a beat that is not callable has
    probability NaN and is never called."""
    thr = float(params["threshold"][0]) if threshold is None else threshold
    prob = np.full(len(beats.callable), np.nan)
    idx = np.flatnonzero(beats.callable)
    if idx.size:
        prob[idx] = predict_proba(params, beats.features[idx])
    return prob, apply_threshold(prob, beats.callable, thr)


# --- training (scikit-learn, lazily) ---------------------------------------------------------------


def make_folds(v_counts: dict[str, int], k: int = FOLDS) -> list[list[str]]:
    """Record-grouped folds: records sorted by V count (most first) and dealt out in a snake, so
    every fold has V-rich and V-poor records; deterministic, no beat-level split."""
    order = sorted(v_counts, key=lambda n: (-v_counts[n], n))
    folds: list[list[str]] = [[] for _ in range(k)]
    for j, name in enumerate(order):
        rnd, pos = divmod(j, k)
        folds[pos if rnd % 2 == 0 else k - 1 - pos].append(name)
    return [sorted(f) for f in folds]


def export_trees(model: Any) -> dict[str, np.ndarray]:
    """Arrays of a fitted HistGradientBoostingClassifier: one row per tree, padded to the largest."""
    trees = [p[0].nodes for p in model._predictors]  # one tree per iteration (binary problem)
    width = max(len(t) for t in trees)
    out = {
        "tree_feature": np.full((len(trees), width), -1, dtype=np.int16),
        "tree_threshold": np.zeros((len(trees), width), dtype=np.float64),
        "tree_left": np.zeros((len(trees), width), dtype=np.int16),
        "tree_right": np.zeros((len(trees), width), dtype=np.int16),
        "tree_value": np.zeros((len(trees), width), dtype=np.float64),
    }
    for i, nodes in enumerate(trees):
        leaf = nodes["is_leaf"].astype(bool)
        m = len(nodes)
        out["tree_feature"][i, :m] = np.where(leaf, -1, nodes["feature_idx"])
        out["tree_threshold"][i, :m] = nodes["num_threshold"]
        out["tree_left"][i, :m] = np.where(leaf, 0, nodes["left"])
        out["tree_right"][i, :m] = np.where(leaf, 0, nodes["right"])
        out["tree_value"][i, :m] = np.where(leaf, nodes["value"], 0.0)
    out["baseline"] = np.asarray(model._baseline_prediction, dtype=np.float64).reshape(-1)[:1]
    out["max_depth"] = np.array([int(GBM["max_depth"])], dtype=np.int32)
    return out


def _fit(features: np.ndarray, y: np.ndarray) -> Any:
    from sklearn.ensemble import HistGradientBoostingClassifier

    return HistGradientBoostingClassifier(**GBM).fit(features, y)


def golden_inputs(seed: int = SEED) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Eight seeded SYNTHETIC snippets of one window, their context rows and the window ids:
    parity vectors for the feature code and the trees, never recordings."""
    rng = np.random.default_rng(seed)
    t = (np.arange(SNIPPET) - HALF) / FS
    snips = np.zeros((N_GOLDEN, SNIPPET), dtype=np.float64)
    for i in range(N_GOLDEN):
        wide = i % 3 == 2
        sigma = 0.028 if wide else 0.012
        amp = (3.0 if wide else 1.5) * (-1.0 if i % 4 == 3 else 1.0)
        snips[i] = amp * np.exp(-0.5 * (t / sigma) ** 2) + rng.normal(0.0, 0.05, SNIPPET)
        snips[i] += (0.3 if wide else 0.15) * np.exp(-0.5 * ((t - 0.15) / 0.05) ** 2) * -amp
    ctx = np.zeros((N_GOLDEN, 4), dtype=np.float32)
    ctx[:, 0] = ctx[:, 1] = 1.0
    ctx[:, 2] = rng.uniform(-0.45, 0.2, N_GOLDEN)
    ctx[:, 3] = rng.uniform(-0.1, 0.6, N_GOLDEN)
    ctx[6] = [0.0, 0.0, 0.0, 0.0]  # no intervals at all
    ctx[7, 1], ctx[7, 3] = 0.0, 0.0  # no interval after
    return snips.astype(np.float32), ctx, np.zeros(N_GOLDEN, dtype=np.int64)


# --- the training command ---------------------------------------------------------------------------


def cmd_train(args: argparse.Namespace) -> None:
    """Record-grouped cross-validation on DS1, threshold from the out-of-fold predictions, then a
    final fit on all of DS1; writes the weights file and a development report."""
    import scipy.signal as sps

    root = pathlib.Path(args.data) / "mitdb" / "extracted"
    sos = np.asarray(sps.butter(2, [0.5, 40.0], btype="bandpass", fs=FS, output="sos"), np.float64)
    records: dict[str, RecordBeats] = {}
    for name in rb.DS1:
        x, pos, sym = pvc.mitdb_beats(root, name)
        records[name] = record_beats(x, pos, sym, sos)
    v_counts = {n: int((r.table.ref_symbol == pvc.POSITIVE).sum()) for n, r in records.items()}
    folds = make_folds(v_counts)
    assert sorted(sum(folds, [])) == sorted(rb.DS1)

    def arrays(names: list[str]) -> tuple[np.ndarray, np.ndarray]:
        parts = [records[n] for n in names]
        f = np.concatenate([p.features[p.callable] for p in parts])
        y = np.concatenate([p.label[p.callable] for p in parts])
        return f, y

    oof: dict[str, np.ndarray] = {}
    for k, held in enumerate(folds):
        f, y = arrays([n for n in rb.DS1 if n not in held])
        model = _fit(f, y)
        for n in held:
            r = records[n]
            p = np.full(len(r.callable), np.nan)
            idx = np.flatnonzero(r.callable)
            if idx.size:
                p[idx] = model.predict_proba(r.features[idx])[:, 1]
            oof[n] = p
        print(f"fold {k}: {len(held)} records held out, trained on {len(y)} beats", flush=True)

    def rows(threshold: float) -> list[dict[str, Any]]:
        out = []
        for n, r in records.items():
            c = pvc.score_called(r.table, apply_threshold(oof[n], r.callable, threshold))
            out.append({"name": n, "n_v": c["n_v"], "n_nonv": c["n_nonv"], "methods": {"m": c}})
        return out

    f1_by_threshold = {t: pvc.pooled(rows(t), "m")["f1"] for t in THRESHOLDS}
    threshold = pvc.first_best(f1_by_threshold)

    f, y = arrays(list(rb.DS1))
    model = _fit(f, y)
    params = export_trees(model)
    g_snip, g_ctx, g_win = golden_inputs()
    g_feat = beat_features(g_snip, np.ones(N_GOLDEN, dtype=bool), g_win, g_ctx)
    reference = model.predict_proba(g_feat)[:, 1]
    params["golden_snippets"] = g_snip
    params["golden_context"] = g_ctx
    params["golden_features"] = g_feat
    params["golden_proba"] = reference
    assert float(np.abs(predict_proba(params, g_feat) - reference).max()) < 1e-9
    every = np.concatenate([r.features[r.callable] for r in records.values()])
    assert float(np.abs(predict_proba(params, every) - model.predict_proba(every)[:, 1]).max()) < 1e-9
    params["sos"] = sos
    params["threshold"] = np.array([threshold], dtype=np.float32)
    params["half"] = np.array([HALF], dtype=np.int32)
    params["clip"] = np.array([CLIP], dtype=np.float32)
    params["mad_scale"] = np.array([MAD_SCALE], dtype=np.float32)
    params["eps"] = np.array([EPS], dtype=np.float32)
    params["format"] = np.array(["biosentry-pvc-gbm-1"])
    models_dir = pathlib.Path(args.models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    np.savez(models_dir / "weights.npz", **params)
    best = rows(threshold)
    report = {
        "folds": folds,
        "v_counts": v_counts,
        "features": list(FEATURE_NAMES),
        "gbm": GBM,
        "threshold_rule": "argmax of pooled out-of-fold beat-level F1 over the grid (first on a tie)",
        "f1_by_threshold": f1_by_threshold,
        "threshold": threshold,
        "out_of_fold": pvc.pooled(best, "m"),
        "v_reference": sum(r["n_v"] for r in best),
        "v_detected_but_not_callable": int(
            sum(int((r.label & ~r.callable).sum()) for r in records.values())
        ),
        "fp_by_symbol": pvc._merge_counts(r["methods"]["m"]["fp_by_symbol"] for r in best),
        "per_record": {
            r["name"]: {k: v for k, v in r["methods"]["m"].items() if k != "fp_by_symbol"}
            | {"not_callable_v": int((records[r["name"]].label & ~records[r["name"]].callable).sum())}
            for r in best
        },
        "trees": int(params["tree_feature"].shape[0]),
        "nodes": n_nodes(params),
        "weights_sha256": hashlib.sha256((models_dir / "weights.npz").read_bytes()).hexdigest(),
    }
    pathlib.Path(args.out).mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=1)
    (pathlib.Path(args.out) / "pvc_head_dev.json").write_text(text + "\n")
    print(text)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("train")
    p.add_argument("--data", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--models-dir", required=True)
    p.set_defaults(fn=cmd_train)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
