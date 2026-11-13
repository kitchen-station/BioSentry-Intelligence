"""The distilled vitals student (slice M1): split, features, labels, the two MindSpore networks,
selection, fallbacks, exports.

Research only. Needs numpy, MindSpore, scikit-learn (comparison rows only), onnx and onnxruntime
(research/pyproject.toml); nothing else in research/ imports this module. The definitions and the
pre-registered grid are in research/README.md and are implemented here exactly. Synthetic, not
validated.
"""

import hashlib
import json
import multiprocessing
import os
import tempfile
import timeit
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import mindspore as ms
import numpy as np
import onnx
import onnxruntime as ort
from mindspore import Tensor, nn, ops
from numpy.lib.stride_tricks import sliding_window_view
from onnx import TensorProto, helper, numpy_helper
from sklearn.ensemble import HistGradientBoostingClassifier
from threadpoolctl import threadpool_limits

from biosentry.domain.scoring.tier_a import News2Score
from biosentry.domain.scoring.tier_b.baseline import DEFAULTS, baseline_finding

from . import bootstrap
from .alerts import POLICIES, Policy, scan
from .rescore import check_parity, score_row
from .trials import (
    BIN_MINUTES, FILES, MAX_CSV_BYTES, Episode, Row, Trial, TrialError, _int_field, _json, _read, load_trial, show,
)

TRAIN_SEEDS = range(61, 161)
VAL_SEEDS = range(261, 301)
CAL_SEEDS = range(301, 341)  # reserved for conformal calibration (M3): manifest.json only
WARD_V2_SEEDS = range(1, 61)  # ward-v2 and ward-v2-train: never read by training
# days, speed and scenario sha256 of ward-v2: the extension must be the same simulator and scenario.
SHAPE = (30, 60, "95052d021b73da3fa71cc62601a795264b8ffd1cf5c4bf2e6e3ba407f74698a8")
VITALS = ("heart_rate", "respiratory_rate", "spo2", "temperature")
OPSET, IR_VERSION = 13, 7
CLIP = 1e-4  # teacher probabilities are clipped before taking log-odds
PARITY_TOL = 1e-5
TIE = 1e-6  # validation log losses within this count as tied
THREADS = 4
FEATURE_VERSION = "m1-1"  # part of the cache key: bump when feature code changes
THRESHOLDS = tuple(i / 1000 for i in range(1, 10)) + tuple(i / 100 for i in range(1, 100))
CHART = POLICIES[0]
LAG_1H, LAG_4H, WINDOW = 4, 16, 16  # bins
CHUNK = 500_000

# Internal feature columns (one row per scored bin, 56). The model inputs are selections of them.
_FULL = [f"{v}{s}" for v in VITALS for s in ("", "_missing", "_z", "_z_missing")] + [
    "news2_total", "on_oxygen", "consciousness_not_alert"]
_TREND = [f"{v}_{k}{s}" for v in VITALS for k in ("d1h", "d4h") for s in ("", "_missing")]
_WIN = [f"{v}_{k}{s}" for v in VITALS for k in ("mean4h", "slope4h") for s in ("", "_missing")]
_N2 = ["news2_d1h", "news2_d1h_missing", "news2_d1h_without_temperature_points",
       "news2_d1h_without_temperature_points_missing"]
_INTERNAL = _FULL + ["temperature_points"] + _TREND + _WIN + _N2
_IDX = {n: i for i, n in enumerate(_INTERNAL)}
_BASE_N = len(_FULL) + 1
_DERIVED = "news2_total_minus_its_temperature_points"  # not stored: total minus temperature points
KINDS = ("base", "trend", "teacher")


@lru_cache(maxsize=None)
def _names(kind: str, temp: bool) -> tuple[str, ...]:
    vs = VITALS if temp else VITALS[:3]
    out = [f"{v}{s}" for v in vs for s in ("", "_missing", "_z", "_z_missing")]
    out += ["news2_total" if temp else _DERIVED, "on_oxygen", "consciousness_not_alert"]
    if kind == "trend":
        out += [f"{v}_{k}{s}" for v in vs for k in ("d1h", "d4h") for s in ("", "_missing")]
    elif kind == "teacher":
        for v in vs:
            out += [f"{v}_{k}{s}" for k in ("d1h", "d4h", "mean4h", "slope4h") for s in ("", "_missing")]
        out += _N2[:2] if temp else _N2[2:]
    elif kind != "base":
        raise ValueError(kind)
    return tuple(out)


def feature_names(temp: bool, kind: str = "base") -> list[str]:
    return list(_names(kind, temp))


def view(x: np.ndarray, temp: bool, kind: str = "base") -> np.ndarray:
    """The model's input columns from the internal matrix (same dtype as x)."""
    names = _names(kind, temp)
    out = np.empty((len(x), len(names)), dtype=x.dtype)
    for j, n in enumerate(names):
        out[:, j] = x[:, _IDX["news2_total"]] - x[:, _IDX["temperature_points"]] if n == _DERIVED else x[:, _IDX[n]]
    return out


def patient_features(rows: Mapping[int, Row]) -> tuple[list[int], np.ndarray]:
    """One stay: the scored (RANKED) bins and their base feature rows (19 inputs and the NEWS2
    temperature points).

    Row t reads bins <= t only: the baseline is `baseline_finding` on the series up to t. A missing
    value is 0 with its flag set; nothing is filled in. NOT RANKED bins get no row (abstention) but
    their values still feed the baseline series.
    """
    minutes: list[int] = []
    feats: list[list[float]] = []
    if rows:
        series: dict[str, list[float | None]] = {v: [] for v in VITALS}
        for minute in range(min(rows), max(rows) + 1, BIN_MINUTES):
            r = rows.get(minute)
            for v in VITALS:
                series[v].append(None if r is None else getattr(r, v))
            if r is None or not r.ranked:
                continue
            f: list[float] = []
            for v in VITALS:
                x = getattr(r, v)
                f += [0.0, 1.0] if x is None else [float(x), 0.0]
                res = baseline_finding(v, series[v], DEFAULTS)
                f += [res[1].z, 0.0] if isinstance(res, tuple) else [0.0, 1.0]
            score = score_row(r)
            if not isinstance(score, News2Score):
                raise TrialError(f"patient {show(r.patient)} minute {minute}: ranked but NEWS2 abstains")
            temp_points = sum(p.score for p in score.parameters if p.parameter == "temperature")
            f += [float(score.total), float(r.on_oxygen), float(r.consciousness != "A"), float(temp_points)]
            minutes.append(minute)
            feats.append(f)
    return minutes, np.array(feats, dtype=np.float64).reshape(len(minutes), _BASE_N)


def stay_features(rows: Mapping[int, Row]) -> tuple[list[int], np.ndarray]:
    """One stay: the scored bins and all 56 internal features (base, trend, window, NEWS2 change).

    Every input at bin t reads bins <= t of the same stay. A lag or window bin is any cohort row at
    that minute with a value, RANKED or not; a missing lag is 0 with its flag set.
    """
    minutes, base = patient_features(rows)
    if not minutes:
        return minutes, np.zeros((0, len(_INTERNAL)))
    lo = min(rows)
    n = (max(rows) - lo) // BIN_MINUTES + 1
    vals = np.full((len(VITALS), n), np.nan)
    tot = np.full((2, n), np.nan)  # NEWS2 total, and the total without its temperature points
    for m, r in rows.items():
        i = (m - lo) // BIN_MINUTES
        for k, v in enumerate(VITALS):
            x = getattr(r, v)
            if x is not None:
                vals[k, i] = x
        if r.ranked:
            s = score_row(r)
            if isinstance(s, News2Score):
                tp = sum(p.score for p in s.parameters if p.parameter == "temperature")
                tot[:, i] = (s.total, s.total - tp)
    idx = (np.asarray(minutes) - lo) // BIN_MINUTES
    pad = lambda a: np.concatenate([np.full((a.shape[0], WINDOW), np.nan), a], axis=1)  # noqa: E731
    pv, pt = pad(vals), pad(tot)
    hours = np.arange(WINDOW) * (BIN_MINUTES / 60)

    def with_flag(d: np.ndarray) -> list[np.ndarray]:
        gone = np.isnan(d)
        return [np.where(gone, 0.0, d), gone.astype(np.float64)]

    trend: list[np.ndarray] = []
    win: list[np.ndarray] = []
    for k in range(len(VITALS)):
        cur = pv[k, WINDOW + idx]
        trend += with_flag(cur - pv[k, WINDOW - LAG_1H + idx]) + with_flag(cur - pv[k, WINDOW - LAG_4H + idx])
        w = sliding_window_view(pv[k], WINDOW)[idx + 1]  # bins t-15..t
        ok = ~np.isnan(w)
        cnt = ok.sum(axis=1)
        mean = np.where(ok, w, 0.0).sum(axis=1) / np.maximum(cnt, 1)
        xm = (ok * hours).sum(axis=1) / np.maximum(cnt, 1)
        dx = np.where(ok, hours - xm[:, None], 0.0)
        sxx = (dx * dx).sum(axis=1)
        sxy = (dx * np.where(ok, w - mean[:, None], 0.0)).sum(axis=1)
        fit = (cnt >= 4) & (sxx > 0)
        win += [np.where(cnt > 0, mean, 0.0), (cnt == 0).astype(np.float64),
                np.where(fit, sxy / np.where(fit, sxx, 1.0), 0.0), (~fit).astype(np.float64)]
    n2 = with_flag(pt[0, WINDOW + idx] - pt[0, WINDOW - LAG_1H + idx]) + \
        with_flag(pt[1, WINDOW + idx] - pt[1, WINDOW - LAG_1H + idx])
    return minutes, np.hstack([base, np.column_stack(trend + win + n2)])


def patient_labels(minutes: Sequence[int], episode: Episode) -> np.ndarray:
    """1 from episode start to fully in, -1 (excluded) after fully in, 0 otherwise."""
    y = np.zeros(len(minutes), dtype=np.int8)
    if episode.kind is not None:
        m = np.asarray(minutes, dtype=np.float64)
        full_in = episode.start + episode.ramp_minutes
        y[(m >= episode.start) & (m <= full_in)] = 1
        y[m > full_in] = -1
    return y


def _by_patient(trial: Trial) -> dict[str, dict[int, Row]]:
    out: dict[str, dict[int, Row]] = defaultdict(dict)
    for r in trial.rows:
        out[r.patient][r.minute] = r
    return out


# --- one trial's features, cached -------------------------------------------------------------

@dataclass(frozen=True)
class TrialFeatures:
    """All scored bins of a trial (every stay, in episodes.csv order), and the plain row index
    needed to run the alert rule. y: 0/1, -1 after fully in, -2 starting patient (left out of
    training and validation rows, kept for alert burden). x is float32."""

    seed: int
    patient_days: float
    n_stays: int
    x: np.ndarray
    y: np.ndarray
    pid: np.ndarray
    minute: np.ndarray
    all_pid: np.ndarray  # every cohort row (RANKED or not), sorted by stay then minute
    all_minute: np.ndarray
    all_chart: np.ndarray  # the NEWS2 chart trigger's state on that row


_ARRAYS = ("x", "y", "pid", "minute", "all_pid", "all_minute", "all_chart")


def trial_features(trial: Trial) -> TrialFeatures:
    """Label-blind features: only the cohort rows feed x. `episodes.csv` gives the labels."""
    by = _by_patient(trial)
    xs, ys, pids, mins, a_pid, a_min, a_chart = [], [], [], [], [], [], []
    for pi, e in enumerate(trial.episodes):
        rows = by.get(e.patient, {})
        minutes, x = stay_features(rows)
        y = patient_labels(minutes, e)
        if e.admit_minute == 0:
            y[:] = -2
        xs.append(x.astype(np.float32))
        ys.append(y)
        pids.append(np.full(len(minutes), pi, np.int32))
        mins.append(np.asarray(minutes, np.int32))
        every = sorted(rows)
        a_pid.append(np.full(len(every), pi, np.int32))
        a_min.append(np.asarray(every, np.int32))
        a_chart.append(np.asarray([CHART.alerting(rows[m]) for m in every], bool))
    cat = lambda parts, dt: np.concatenate(parts) if parts else np.zeros(0, dt)  # noqa: E731
    return TrialFeatures(
        trial.seed, trial.patient_days, len(trial.episodes),
        np.vstack(xs) if xs else np.zeros((0, len(_INTERNAL)), np.float32), cat(ys, np.int8),
        cat(pids, np.int32), cat(mins, np.int32), cat(a_pid, np.int32), cat(a_min, np.int32),
        cat(a_chart, bool))


def cache_key(folder: str | os.PathLike) -> str:
    """sha256 of the trial's four files and the feature-code version."""
    h = hashlib.sha256(FEATURE_VERSION.encode())
    for name in FILES:
        h.update(hashlib.sha256(_read(Path(folder), name, MAX_CSV_BYTES).encode()).digest())
    return h.hexdigest()


def _load_cached(path: Path) -> TrialFeatures | None:
    # shortcut: the cache key covers the trial's files and the feature-code version, not the cache file
    # itself, so a hand-edited .npz is trusted; delete the cache folder if it may have been touched
    try:
        with np.load(path) as z:
            return TrialFeatures(int(z["seed"]), float(z["patient_days"]), int(z["n_stays"]),
                                 **{k: z[k] for k in _ARRAYS})
    except (OSError, ValueError, KeyError):
        return None  # a missing or damaged cache entry is only a miss


def extract(folder: str, cache: str | None = None) -> TrialFeatures:
    """One trial folder to its features: parity-checked, and cached outside the repo if asked."""
    path = Path(cache) / f"{cache_key(folder)}.npz" if cache else None
    if path is not None and (hit := _load_cached(path)) is not None:
        return hit
    trial = load_trial(folder)
    check_parity(trial)
    tf = trial_features(trial)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.stem + ".tmp.npz")
        np.savez(tmp, seed=tf.seed, patient_days=tf.patient_days, n_stays=tf.n_stays,
                 **{k: getattr(tf, k) for k in _ARRAYS})
        os.replace(tmp, path)
    return tf


def extract_all(folders: Sequence[str], cache: str | None, workers: int | None) -> list[TrialFeatures]:
    """Results in the order of `folders` (the caller sorts them by seed). workers <= 1: in process."""
    workers = min(4, os.cpu_count() or 1) if workers is None else workers
    if workers <= 1 or len(folders) <= 1:
        return [extract(f, cache) for f in folders]
    # spawn, not fork: the parent may already hold OpenMP threads
    with ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("spawn")) as ex:
        return list(ex.map(extract, folders, [cache] * len(folders)))


# --- the split, enforced where data is read ---------------------------------------------------

def _manifest(folder: str) -> tuple[int, dict]:
    """Only manifest.json is opened: enough to skip or refuse a folder before its data is read."""
    path = Path(folder)
    if not path.is_dir():
        raise TrialError(f"{show(path.name)}: not a folder")
    try:
        m = _json(path, "manifest.json")
        return _int_field(m, "seed", "manifest.json", 0, 2**31 - 1), m
    except TrialError as e:
        raise TrialError(f"{show(path.name)}: {e}") from None


def manifest_seed(folder: str) -> int:
    return _manifest(folder)[0]


def _check_shape(folder: str, m: dict) -> None:
    if (m.get("days"), m.get("speed"), m.get("scenario_sha256")) != SHAPE:
        raise TrialError(f"{show(Path(folder).name)}: days, speed or scenario hash differ from ward-v2's "
                         f"({SHAPE[0]}, {SHAPE[1]}, {SHAPE[2][:8]}...)")


def _seed_range(r: range) -> str:
    return f"{r.start} to {r.stop - 1}"


def load_for_training(folders: Sequence[str], cache: str | None = None, workers: int | None = None
                      ) -> tuple[list[TrialFeatures], list[TrialFeatures], dict[str, list[int]]]:
    """(training trials, validation trials, skipped seeds by reason), each in sorted seed order.

    Seeds 61-160 train, 261-300 validate. Seeds 301-340 (calibration) and 1-60 (ward-v2) are
    skipped after reading only manifest.json. Every other seed is refused, as is a trial whose
    days, speed or scenario hash differ from ward-v2's.
    """
    skipped: dict[str, list[int]] = {"ward_v2": [], "calibration": []}
    jobs: dict[int, str] = {}
    for folder in sorted(folders):
        seed, m = _manifest(folder)
        if seed in WARD_V2_SEEDS:
            skipped["ward_v2"].append(seed)
        elif seed in CAL_SEEDS:
            skipped["calibration"].append(seed)
        elif seed not in TRAIN_SEEDS and seed not in VAL_SEEDS:
            raise TrialError(f"{show(Path(folder).name)}: seed {seed} is not used here (training "
                             f"{_seed_range(TRAIN_SEEDS)}, validation {_seed_range(VAL_SEEDS)})")
        elif seed in jobs:
            raise TrialError(f"{show(Path(folder).name)}: seed {seed} given twice")
        else:
            _check_shape(folder, m)
            jobs[seed] = folder
    seeds = sorted(jobs)
    feats = extract_all([jobs[s] for s in seeds], cache, workers)
    for s, tf in zip(seeds, feats):
        if tf.seed != s:  # the manifest changed between the two reads
            raise TrialError(f"seed {s}: seed changed while reading")
    train = [f for f in feats if f.seed in TRAIN_SEEDS]
    val = [f for f in feats if f.seed in VAL_SEEDS]
    if not train or not val:
        raise TrialError(f"need at least one trial of seeds {_seed_range(TRAIN_SEEDS)} "
                         f"and one of seeds {_seed_range(VAL_SEEDS)}")
    return train, val, {k: sorted(v) for k, v in skipped.items()}


def _refuse_outside(trials: Sequence[TrialFeatures], split: range) -> None:
    for t in trials:
        if t.seed not in split:
            raise TrialError(f"seed {t.seed} is not in seeds {_seed_range(split)}")


# --- matrices -----------------------------------------------------------------------------------

def _stack(tfs: Sequence[TrialFeatures], kind: str, temp: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """float32 inputs, labels and global patient ids of the labelled bins (y >= 0): starting
    patients and bins after fully in are left out. Built trial by trial, no second copy."""
    sel = [tf.y >= 0 for tf in tfs]
    n = sum(int(s.sum()) for s in sel)
    x = np.empty((n, len(_names(kind, temp))), np.float32)
    y = np.empty(n, np.int8)
    g = np.empty(n, np.int64)
    a = 0
    for i, (tf, s) in enumerate(zip(tfs, sel)):
        m = int(s.sum())
        x[a:a + m] = view(tf.x[s], temp, kind)
        y[a:a + m] = tf.y[s]
        g[a:a + m] = i * 100_000 + tf.pid[s]
        a += m
    return x, y, g


def _moments(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Mean and std of the columns in float64 (chunked, no float64 copy of x), as float32.
    A standard deviation of 0 becomes 1."""
    mean = np.zeros(x.shape[1])
    for i in range(0, len(x), CHUNK):
        mean += x[i:i + CHUNK].sum(axis=0, dtype=np.float64)
    mean /= len(x)
    var = np.zeros(x.shape[1])
    for i in range(0, len(x), CHUNK):
        var += ((x[i:i + CHUNK] - mean) ** 2).sum(axis=0)
    std = np.sqrt(var / len(x))
    std[std <= 1e-9 * np.maximum(1.0, np.abs(mean))] = 1.0  # constant column (rounding can leave ~1e-17)
    return mean.astype(np.float32), std.astype(np.float32)


def _standardise(x: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    """In place, with the same float32 operations as the graph."""
    np.subtract(x, mean, out=x)
    np.divide(x, std, out=x)
    return x


# --- models -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Student:
    kind: str
    mean: np.ndarray  # float32, the standardisation constants (inside the ONNX graph)
    std: np.ndarray
    layers: tuple[tuple[np.ndarray, np.ndarray], ...]  # (W, b) float32; ReLU between, sigmoid last

    def logit(self, x: np.ndarray) -> np.ndarray:
        """The numpy forward pass up to the sigmoid, in float32 and in the ONNX graph's order."""
        h = (x.astype(np.float32) - self.mean) / self.std
        for i, (w, b) in enumerate(self.layers):
            h = h @ w + b
            if i < len(self.layers) - 1:
                h = np.maximum(h, np.float32(0))
        return h[:, 0]

    def forward(self, x: np.ndarray) -> np.ndarray:
        return np.float32(1) / (np.float32(1) + np.exp(-self.logit(x)))

    @property
    def parameters(self) -> int:
        return sum(w.size + b.size for w, b in self.layers)


def to_onnx(s: Student) -> bytes:
    n = len(s.mean)
    inits = [numpy_helper.from_array(s.mean.reshape(1, n), "mean"),
             numpy_helper.from_array(s.std.reshape(1, n), "std")]
    nodes = [helper.make_node("Sub", ["x", "mean"], ["centred"]),
             helper.make_node("Div", ["centred", "std"], ["h0"])]
    last = len(s.layers) - 1
    for i, (w, b) in enumerate(s.layers):
        inits += [numpy_helper.from_array(w, f"w{i}"), numpy_helper.from_array(b, f"b{i}")]
        nodes.append(helper.make_node("Gemm", [f"h{i}", f"w{i}", f"b{i}"], [f"g{i}"]))
        if i < last:
            nodes.append(helper.make_node("Relu", [f"g{i}"], [f"h{i + 1}"]))
    nodes.append(helper.make_node("Sigmoid", [f"g{last}"], ["p"]))
    graph = helper.make_graph(nodes, "student_vitals",
                              [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, n])],
                              [helper.make_tensor_value_info("p", TensorProto.FLOAT, [1, 1])], inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", OPSET)],
                              producer_name="biosentry-research", ir_version=IR_VERSION)
    onnx.checker.check_model(model, full_check=True)
    return model.SerializeToString()


def onnx_ops(data: bytes) -> list[str]:
    return sorted({node.op_type for node in onnx.load_from_string(data).graph.node})


def session(data: bytes) -> ort.InferenceSession:
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 1
    return ort.InferenceSession(data, opts, providers=["CPUExecutionProvider"])


def onnx_predict(sess: ort.InferenceSession, x: np.ndarray) -> np.ndarray:
    """One row at a time: the graph's input shape is static [1, n_features]."""
    x = x.astype(np.float32)
    return np.array([sess.run(None, {"x": x[i:i + 1]})[0][0, 0] for i in range(len(x))], dtype=np.float32)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- alert policies and the matched-burden threshold ------------------------------------------

class Scorer:
    """rows of one stay -> {scored minute: probability}; memoised per stay, causal per bin.
    Used on trials read directly (the held-out scoring); `model_burden` does the same on arrays."""

    _features: dict[int, tuple[Mapping[int, Row], list[int], np.ndarray]] = {}

    def __init__(self, predict: Callable[[np.ndarray], np.ndarray], temp: bool, kind: str = "base"):
        self.predict, self.temp, self.kind = predict, temp, kind
        self._memo: dict[int, tuple[Mapping[int, Row], dict[int, float]]] = {}

    @classmethod
    def clear(cls) -> None:
        cls._features.clear()

    def __call__(self, rows: Mapping[int, Row]) -> dict[int, float]:
        hit = self._memo.get(id(rows))
        if hit is not None and hit[0] is rows:
            return hit[1]
        f = Scorer._features.get(id(rows))
        if f is None or f[0] is not rows:
            f = (rows, *stay_features(rows))
            Scorer._features[id(rows)] = f
        _, minutes, x = f
        # float32 like the stored matrices, so training and scoring see the same numbers
        out = (dict(zip(minutes, map(float, self.predict(view(x.astype(np.float32), self.temp, self.kind)))))
               if minutes else {})
        self._memo[id(rows)] = (rows, out)
        return out


def prob_policy(name: str, scorer: Callable[[Mapping[int, Row]], dict[int, float]], threshold: float,
                with_chart: bool = False) -> Policy:
    """Alerting when p >= threshold on a scored bin (or, with_chart, when NEWS2 escalates)."""
    def states(rows: Mapping[int, Row]) -> dict[int, bool]:
        p = scorer(rows)
        return {m: (m in p and p[m] >= threshold) or (with_chart and CHART.alerting(rows[m])) for m in rows}

    return Policy(name, lambda row: False, CHART.abstains, states)


Burden = tuple[int, int]  # (alerts, bins alerting)


def match_threshold(burden: Mapping[float, Burden], chart: Burden) -> float | None:
    """The smallest threshold whose alerts AND time in alert (same bins) are both at most the
    NEWS2 chart trigger's on the same seeds."""
    return min((t for t, (a, on) in burden.items() if a <= chart[0] and on <= chart[1]), default=None)


def _stay_slices(tf: TrialFeatures) -> tuple[np.ndarray, np.ndarray]:
    stays = np.arange(tf.n_stays + 1)
    return np.searchsorted(tf.all_pid, stays), np.searchsorted(tf.pid, stays)


def _scan(minutes: list[int], rows: dict, state: Sequence[bool]) -> Burden:
    fired, on = scan(Policy("t", lambda r: False, lambda r: False, lambda _r: dict(zip(minutes, state))), rows)
    return len(fired), len(on)


def chart_burden(trials: Sequence[TrialFeatures]) -> tuple[Burden, int, float]:
    """NEWS2 chart trigger on the given trials: (alerts, bins alerting), cohort rows, patient-days."""
    alerts = on = bins = 0
    for tf in trials:
        a, _ = _stay_slices(tf)
        for s in range(tf.n_stays):
            ms = tf.all_minute[a[s]:a[s + 1]].tolist()
            f, o = _scan(ms, dict.fromkeys(ms), tf.all_chart[a[s]:a[s + 1]].tolist())
            alerts, on = alerts + f, on + o
        bins += len(tf.all_minute)
    return (alerts, on), bins, sum(tf.patient_days for tf in trials)


def model_burden(trials: Sequence[TrialFeatures], probs: Sequence[np.ndarray]) -> dict[float, Burden]:
    """Alerts and bins alerting per threshold ("alerting when p >= t" on a scored bin; a bin with
    no probability is not alerting), summed over the trials. `probs[i]` is aligned with trials[i].x."""
    tot = {t: [0, 0] for t in THRESHOLDS}
    for tf, p in zip(trials, probs):
        a, c = _stay_slices(tf)
        p = np.asarray(p, dtype=np.float64)  # float32 -> float64 is exact, as float(p) is
        for s in range(tf.n_stays):
            ps = p[c[s]:c[s + 1]]
            if not len(ps):
                continue
            am = tf.all_minute[a[s]:a[s + 1]]
            ms = am.tolist()
            rows = dict.fromkeys(ms)
            pos = np.searchsorted(am, tf.minute[c[s]:c[s + 1]])
            top = ps.max()
            for t in THRESHOLDS:
                if t > top:  # thresholds are ascending: nothing alerts above the stay's maximum
                    break
                hit = np.zeros(len(am), bool)
                hit[pos[ps >= t]] = True
                f, o = _scan(ms, rows, hit.tolist())
                tot[t][0] += f
                tot[t][1] += o
    return {t: (a, o) for t, (a, o) in tot.items()}


def row_loss(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=np.float64), 1e-7, 1 - 1e-7)
    y = y.astype(np.float64)
    return -(y * np.log(p) + (1 - y) * np.log1p(-p))


def paired_gate(loss: np.ndarray, refs: Mapping[str, np.ndarray], groups: np.ndarray,
                resamples: int = bootstrap.RESAMPLES) -> dict[str, dict]:
    """Per reference: mean of (loss - reference loss), and the 95% patient-level bootstrap interval
    (the repo's SplitMix64, seed 20261112). "Below" means the 97.5th percentile is under 0."""
    _, inv = np.unique(groups, return_inverse=True)
    n = np.bincount(inv).astype(np.float64).tolist()
    sums = {k: np.bincount(inv, weights=loss - r) for k, r in refs.items()}
    units = {k: list(zip(s.tolist(), n)) for k, s in sums.items()}
    iv = bootstrap.intervals(units, {"d": lambda s: s[0] / s[1] if s[1] else None}, resamples=resamples)
    out = {}
    for k, s in sums.items():
        lo_hi = iv[k, "d"]
        out[k] = {"difference": round(float(s.sum() / sum(n)), 6),
                  "lower_2_5": None if lo_hi is None else round(lo_hi[0], 6),
                  "upper_97_5": None if lo_hi is None else round(lo_hi[1], 6),
                  "passes": lo_hi is not None and lo_hi[1] < 0}
    return out


def _round(x: float) -> float:
    return round(float(x), 6)


def load_for_evaluation(folders: Sequence[str], cache: str | None = None, workers: int | None = None
                        ) -> tuple[list[TrialFeatures], list[str]]:
    """(training trials 61-160 to refit on, ward-v2 folders of seeds 11-30 to score), by manifest.

    Refuses ward-v2 seeds 1-10 (no longer used) and every seed not named here, except that seeds
    31-60 and 261-340 are skipped after reading only manifest.json (nothing above 300 is opened).
    """
    train_jobs: dict[int, str] = {}
    held: list[str] = []
    seen: set[int] = set()
    for folder in sorted(folders):
        seed, m = _manifest(folder)
        name = show(Path(folder).name)
        if seed in seen:
            raise TrialError(f"{name}: seed {seed} given twice")
        seen.add(seed)
        if seed in range(1, 11):
            raise TrialError(f"{name}: ward-v2 seeds 1 to 10 are no longer used by the student")
        if seed in range(11, 31):
            _check_shape(folder, m)
            held.append(folder)
        elif seed in TRAIN_SEEDS:
            _check_shape(folder, m)
            train_jobs[seed] = folder
        elif seed not in range(31, 61) and seed not in VAL_SEEDS and seed not in CAL_SEEDS:
            raise TrialError(f"{name}: seed {seed} is not used here")
    seeds = sorted(train_jobs)
    train = extract_all([train_jobs[s] for s in seeds], cache, workers)
    if [t.seed for t in train] != seeds:
        raise TrialError("a seed changed while reading")
    return train, held



# --- the MindSpore networks ---------------------------------------------------------------------

MS_THREADS = 1  # the byte-reproducibility promise is for one kernel thread only
FWD_CHUNK = 65536
LOGIT_CLIP = float(np.log((1 - CLIP) / CLIP))  # 9.2102: teacher logits are clipped here
PREFIX = "m1"
# seconds the contract's central projection allows for everything but the teachers: students 0.5 h,
# reference (b) 0.25 h, reference (d) 0.25 h, features 0.2 h
REST_S = {"students": 1800.0, "reference_b": 900.0, "reference_d": 900.0, "features": 720.0}
CUTS = ("drop_reference_d", "teacher_epochs_10", "teacher_decay_largest_only")


_READY: list[bool] = []


def init_runtime() -> None:
    """GRAPH_MODE on CPU, one kernel thread (more threads would change the bytes). MindSpore refuses
    to set the thread count twice, so this runs once per process."""
    if _READY:
        return
    ms.set_device("CPU")
    ms.set_context(mode=ms.GRAPH_MODE)
    ms.device_context.cpu.op_tuning.threads_num(MS_THREADS)
    _READY.append(True)


@dataclass(frozen=True)
class Grid:
    """The pre-registered values are the defaults; tests pass small ones."""

    teacher_widths: tuple[int, ...] = (256, 256)
    teacher_decays: tuple[float, ...] = (1e-5, 1e-4)
    teacher_batch: int = 1024
    teacher_epochs: int = 15
    teacher_patience: int = 3
    student_widths: tuple[int, ...] = (8, 16, 32)
    student_inputs: tuple[str, ...] = ("base", "trend")
    student_decay: float = 1e-5
    student_batch: int = 4096
    student_epochs: int = 8
    student_patience: int = 2
    learning_rate: float = 1e-3
    parity_rows: int = 1000
    hgb_b_iter: int = 500
    hgb_d_iter: int = 2000
    hgb_d_min_leaf: int = 2000
    reference_d: bool = True
    budget_seconds: float = 3 * 3600.0

    def as_dict(self) -> dict:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: Mapping) -> "Grid":
        return cls(**{k: tuple(v) if isinstance(v, list) else v for k, v in d.items()})


class Cell(nn.Cell):
    """The exported graph: Sub(mean), Div(std), Dense, ReLU, ..., Dense, then Sigmoid (or logits)."""

    def __init__(self, s: "Student", sigmoid: bool = True):
        super().__init__()
        self.mean = Tensor(s.mean.reshape(1, -1))
        self.std = Tensor(s.std.reshape(1, -1))
        cells: list = []
        for i, (w, b) in enumerate(s.layers):
            cells.append(nn.Dense(w.shape[0], w.shape[1], weight_init=Tensor(np.ascontiguousarray(w.T)),
                                  bias_init=Tensor(b)))
            if i < len(s.layers) - 1:
                cells.append(nn.ReLU())
        self.net = nn.SequentialCell(cells)
        self.sigmoid = sigmoid

    def construct(self, x):
        z = self.net((x - self.mean) / self.std)
        return ops.sigmoid(z) if self.sigmoid else z


def forward(cell: nn.Cell, x: np.ndarray) -> np.ndarray:
    """Output column of `cell` on float32 rows, in fixed-size chunks (the last one padded, so the
    graph compiles for one shape per size class and not once per call)."""
    n = len(x)
    out = np.empty(n, np.float32)
    if n == 0:
        return out
    chunk = min(FWD_CHUNK, max(256, 1 << (n - 1).bit_length()))
    pad = np.zeros((chunk, x.shape[1]), np.float32)
    for i in range(0, n, chunk):
        part = x[i:i + chunk]
        pad[:len(part)] = part
        out[i:i + len(part)] = cell(Tensor(pad.copy())).asnumpy()[:len(part), 0]
    return out


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z.astype(np.float64)))


def log_loss(y: np.ndarray, p: np.ndarray) -> float:
    return float(row_loss(y, p).mean())


def logit_of(p: float) -> float:
    return float(np.log(p / (1 - p)))


class Stopper:
    """Early stopping on a loss sequence: a strictly lower loss is a new best (a tie keeps the
    earlier epoch); `done` after `patience` epochs without one."""

    def __init__(self, patience: int):
        self.patience, self.best, self.best_epoch, self.epoch, self.bad = patience, float("inf"), 0, 0, 0

    def update(self, loss: float) -> bool:
        self.epoch += 1
        if loss < self.best:
            self.best, self.best_epoch, self.bad = loss, self.epoch, 0
            return True
        self.bad += 1
        return False

    @property
    def done(self) -> bool:
        return self.bad >= self.patience


@dataclass
class Plan:
    """The pre-registered budget rule. After the first teacher's first epoch the total is projected
    from that epoch and the caps; while it exceeds the budget the cuts are applied in order."""

    g: Grid
    epochs: int = 0
    decays: tuple[float, ...] = ()
    reference_d: bool = True
    applied: list[str] | None = None
    projected: float | None = None
    seen: bool = False

    def __post_init__(self) -> None:
        self.epochs, self.reference_d = self.g.teacher_epochs, self.g.reference_d
        self.decays = tuple(sorted(self.g.teacher_decays, reverse=True))  # largest first: a cut keeps it
        self.applied = []

    def project(self, epoch_s: float) -> float:
        rest = REST_S["students"] + REST_S["reference_b"] + REST_S["features"]
        rest += REST_S["reference_d"] if self.reference_d else 0.0
        return 2 * len(self.decays) * self.epochs * epoch_s + rest

    def first_epoch(self, epoch: int, secs: float) -> None:
        if self.seen or epoch != 1:
            return
        self.seen = True
        self.projected = self.project(secs)  # shortcut: epoch 1 includes graph compile, a slight overestimate
        for cut in CUTS:
            if self.project(secs) <= self.g.budget_seconds:
                break
            if cut == "drop_reference_d" and self.reference_d:
                self.reference_d = False
            elif cut == "teacher_epochs_10" and self.epochs > 10:
                self.epochs = 10
            elif cut == "teacher_decay_largest_only" and len(self.decays) > 1:
                self.decays = self.decays[:1]
            else:
                continue
            self.applied.append(cut)


@dataclass
class Fit:
    layers: tuple[tuple[np.ndarray, np.ndarray], ...]
    best_epoch: int
    val_losses: list[float]
    train_losses: list[float] | None = None  # mean batch loss of each epoch
    epoch_seconds: list[float] | None = None  # wall time of each epoch, validation pass included


def _mlp(n_in: int, widths: Sequence[int], bias: float) -> nn.SequentialCell:
    ms.set_seed(0)
    cells: list = []
    prev = n_in
    for w in widths:
        cells += [nn.Dense(prev, w), nn.ReLU()]
        prev = w
    cells.append(nn.Dense(prev, 1))
    net = nn.SequentialCell(cells)
    net[len(cells) - 1].bias.set_data(Tensor(np.full(1, bias, np.float32)))
    return net


def _layers(net: nn.SequentialCell) -> tuple[tuple[np.ndarray, np.ndarray], ...]:
    return tuple((np.ascontiguousarray(c.weight.asnumpy().T, dtype=np.float32),
                  np.ascontiguousarray(c.bias.asnumpy(), dtype=np.float32))
                 for c in net.cells() if isinstance(c, nn.Dense))


def fit_net(x: np.ndarray, target: np.ndarray, *, widths: Sequence[int], loss: str, decay: float, batch: int,
            epochs: int | Callable[[], int], patience: int, bias: float, lr: float,
            val: tuple[np.ndarray, np.ndarray] | None = None, log: Callable[[str], None] = lambda s: None,
            tag: str = "fit", on_epoch: Callable[[int, float], None] | None = None) -> Fit:
    """Adam on standardised float32 rows. loss "bce" (target = labels) or "mse" (target = logits);
    weight decay on the weight matrices only. With `val` = (rows, labels): early stopping on the
    validation log loss and the best epoch's weights. Without it: exactly `epochs` epochs (the refit).
    Rows are shuffled each epoch by numpy.random.default_rng(0); a last partial batch is dropped."""
    init_runtime()
    net = _mlp(x.shape[1], widths, bias)
    lossfn = nn.BCEWithLogitsLoss() if loss == "bce" else nn.MSELoss()
    params = net.trainable_params()
    opt = nn.Adam([{"params": [p for p in params if p.name.endswith("weight")], "weight_decay": decay},
                   {"params": [p for p in params if p.name.endswith("bias")], "weight_decay": 0.0}],
                  learning_rate=lr)

    def fwd(xb, tb):
        return lossfn(net(xb), tb)

    grad = ms.value_and_grad(fwd, None, opt.parameters)

    @ms.jit
    def step(xb, tb):
        value, g = grad(xb, tb)
        opt(g)
        return value

    cap = epochs if callable(epochs) else (lambda: epochs)
    t = target.reshape(-1, 1).astype(np.float32)
    rng, n = np.random.default_rng(0), len(x)
    bs = min(batch, n)
    stop, best, losses, epoch = Stopper(patience), None, [], 0
    train_losses, seconds = [], []
    snap = lambda: [p.asnumpy().copy() for p in net.get_parameters()]  # noqa: E731
    while epoch < cap():
        epoch += 1
        t0 = timeit.default_timer()
        perm = rng.permutation(n)
        total, steps = 0.0, 0
        for i in range(0, n - bs + 1, bs):
            idx = perm[i:i + bs]
            total += float(step(Tensor(x[idx]), Tensor(t[idx])).asnumpy())  # the batch loss: no second pass
            steps += 1
        train_losses.append(total / max(steps, 1))
        if val is not None:
            losses.append(log_loss(val[1], _sigmoid(forward(net, val[0]))))
            if stop.update(losses[-1]):
                best = snap()
        secs = timeit.default_timer() - t0  # the whole epoch, validation pass included
        seconds.append(secs)
        log(f"{tag} epoch {epoch} {secs:.1f}s")
        if on_epoch is not None:
            on_epoch(epoch, secs)
        if val is not None and stop.done:
            break
    if val is None:
        return Fit(_layers(net), epoch, [], train_losses, seconds)
    if best is None:  # no finite loss at all: keep the last weights
        best, stop.best_epoch = snap(), epoch
    for p, a in zip(net.get_parameters(), best):
        p.set_data(Tensor(a))
    return Fit(_layers(net), stop.best_epoch, losses, train_losses, seconds)


def weights_sha256(s: Student) -> str:
    h = hashlib.sha256()
    for w, b in s.layers:
        h.update(np.ascontiguousarray(w).tobytes() + np.ascontiguousarray(b).tobytes())
    return h.hexdigest()


def average_precision(y: np.ndarray, p: np.ndarray) -> float:
    order = np.argsort(-p, kind="stable")
    hit = y[order] == 1
    if not hit.any():
        return 0.0
    precision = np.cumsum(hit) / np.arange(1, len(hit) + 1)
    return float(precision[hit].mean())


def _student_key(c: Mapping) -> tuple:
    return (c["parameters"], c["inputs"] != "base", -c["weight_decay"])


def _teacher_key(c: Mapping) -> tuple:
    return (c["parameters"], -c["weight_decay"])


def pick(cands: Sequence[Mapping], key: Callable[[Mapping], tuple]) -> Mapping:
    """Lowest validation log loss; losses within TIE count as tied and `key` breaks the tie."""
    best = min(c["exact"] for c in cands)  # unrounded: rounding to 6 decimals happens only in the metadata
    return min((c for c in cands if c["exact"] <= best + TIE), key=key)


def decide(teacher_passes: bool, student_beats_constant: bool, hard_beats_constant: bool) -> str | None:
    """What ships for a variant: the distilled student, else (F1) the best hard-label network if it
    beats the constant, else (F2) nothing."""
    if teacher_passes and student_beats_constant:
        return "distilled_student"
    return "hard_label_mlp" if hard_beats_constant else None


def _hgb(**kw) -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(scoring="loss", random_state=0, **kw)


def _hgb_p(m: HistGradientBoostingClassifier, x: np.ndarray) -> np.ndarray:
    with threadpool_limits(THREADS):
        return m.predict_proba(x)[:, 1]


def reference_old_recipe(x, y, xv, yv, iters: int) -> tuple[np.ndarray, int]:
    """(b) B5's teacher on the base inputs: (validation probabilities, iterations kept)."""
    with threadpool_limits(THREADS):
        m = _hgb(max_iter=iters, early_stopping=True, n_iter_no_change=20).fit(x, y, X_val=xv, y_val=yv)
    return _hgb_p(m, xv), int(m.n_iter_)


def reference_regularised(x, y, xv, yv, g: Grid) -> tuple[np.ndarray, int]:
    """(d) one regularised gradient boosting on the teacher inputs (v1's teacher with 15 leaves):
    early stopping on validation, then a refit at the best iteration. (validation p, iterations)"""
    kw = dict(learning_rate=0.05, l2_regularization=1.0, max_leaf_nodes=15, min_samples_leaf=g.hgb_d_min_leaf)
    with threadpool_limits(THREADS):
        m = _hgb(max_iter=g.hgb_d_iter, early_stopping=True, n_iter_no_change=50, **kw).fit(
            x, y, X_val=xv, y_val=yv)
        b = int(np.argmax(m.validation_score_))  # index 0 is "no trees": a constant
        if b == 0:
            return np.full(len(xv), float(y.mean())), 0
        m = _hgb(max_iter=b, early_stopping=False, **kw).fit(x, y)
    return _hgb_p(m, xv), b


@dataclass
class Result:
    """One variant. `onnx` and `student` are None when nothing ships (F2)."""

    tag: str
    temp: bool
    inputs: str | None
    meta: dict
    onnx: bytes | None = None
    mindir: bytes | None = None
    student: Student | None = None
    teacher: Student | None = None
    parity_x: np.ndarray | None = None
    parity_p: np.ndarray | None = None
    timing_row: np.ndarray | None = None
    exports: dict | None = None
    runlog: dict | None = None  # deterministic per-fit record for the run log
    times: dict | None = None  # every wall-clock number of the variant (the log's `time` object)


@dataclass
class Built:
    results: dict[str, Result]
    cuts: list[str]
    seconds: dict


def _sample(n: int, k: int) -> np.ndarray:
    """k fixed row indices spread over n rows."""
    return np.unique(np.linspace(0, n - 1, min(k, n)).astype(np.int64))


def _prepare(trials: Sequence[TrialFeatures], kind: str, temp: bool):
    """Standardised float32 training rows (in place, no second copy), labels, and the constants."""
    x, y, _ = _stack(trials, kind, temp)
    if not (y == 1).any() or not (y == 0).any():
        raise TrialError("the training bins need at least one positive and one negative bin")
    mean, std = _moments(x)
    return _standardise(x, mean, std), y, mean, std


def _fit_teacher(xt, yt, g: Grid, wd: float, epochs, val, log, tag: str, on_epoch=None) -> Fit:
    return fit_net(xt, yt, widths=g.teacher_widths, loss="bce", decay=wd, batch=g.teacher_batch,
                   epochs=epochs, patience=g.teacher_patience, bias=logit_of(float(yt.mean())),
                   lr=g.learning_rate, val=val, log=log, tag=tag, on_epoch=on_epoch)


def _fit_student(xs, target, loss: str, width: int, g: Grid, val, log, tag: str, epochs=None) -> Fit:
    bias = float(target.mean()) if loss == "mse" else logit_of(float(target.mean()))
    return fit_net(xs, target, widths=(width,), loss=loss, decay=g.student_decay, batch=g.student_batch,
                   epochs=g.student_epochs if epochs is None else epochs, patience=g.student_patience,
                   bias=bias, lr=g.learning_rate, val=val, log=log, tag=tag)


def _soft(teacher: Student, xt: np.ndarray) -> np.ndarray:
    """The teacher's logits on standardised training rows, clipped: the students' only target."""
    n = xt.shape[1]
    ident = Student("teacher", np.zeros(n, np.float32), np.ones(n, np.float32), teacher.layers)
    return np.clip(forward(Cell(ident, sigmoid=False), xt), -LOGIT_CLIP, LOGIT_CLIP).astype(np.float32)


def refit_variant(train: Sequence[TrialFeatures], temp: bool, meta: Mapping, g: Grid,
                  log: Callable[[str], None] = lambda s: None) -> dict:
    """The chosen teacher and student only, each trained for exactly its recorded best epoch count
    (the weights after epoch k of a monitored fit equal those of a k-epoch fit). No validation row
    is read. Returns {"teacher": Student, "student": Student | None}."""
    init_runtime()
    xt, yt, mean, std = _prepare(train, "teacher", temp)
    c = meta["teacher"]["chosen"]
    f = _fit_teacher(xt, yt, g, c["weight_decay"], c["best_epoch"], None, log, f"refit teacher {meta['tag']}")
    teacher, student, sc = Student("teacher", mean, std, f.layers), None, meta["student"]
    if sc is not None:
        soft = _soft(teacher, xt) if sc["loss"] == "mse" else None
        del xt, yt
        xs, ys, mean, std = _prepare(train, sc["inputs"], temp)
        f = _fit_student(xs, ys if soft is None else soft, sc["loss"], sc["width"], g, None, log,
                         f"refit student {meta['tag']}", epochs=sc["best_epoch"])
        student = Student("mlp", mean, std, f.layers)
    return {"teacher": teacher, "student": student}


# --- one variant --------------------------------------------------------------------------------

def _val_stats(y: np.ndarray, z: np.ndarray, zt: np.ndarray, teacher_loss: float) -> dict:
    p = _sigmoid(z)
    loss = log_loss(y, p)
    return {"exact": loss, "val_log_loss": _round(loss), "average_precision": _round(average_precision(y, p)),
            "mean_p": _round(p.mean()),
            "fidelity_rmse_log_odds": _round(np.sqrt(np.mean((np.clip(z, -LOGIT_CLIP, LOGIT_CLIP) - zt) ** 2))),
            "log_loss_minus_teacher": _round(loss - teacher_loss)}


def _burden_record(val: Sequence[TrialFeatures], predict: Callable[[np.ndarray], np.ndarray], temp: bool,
                   kind: str, chart) -> tuple[dict, float]:
    """(threshold record, validation log loss of the same probabilities against the true labels).
    `predict` maps raw float32 rows to p; the threshold rule runs on exactly those probabilities."""
    probs = [predict(view(tf.x, temp, kind)) for tf in val]
    burden = model_burden(val, probs)
    t = match_threshold(burden, chart[0])
    keep = [tf.y >= 0 for tf in val]  # the rows _stack uses, in the same order
    loss = log_loss(np.concatenate([tf.y[k] for tf, k in zip(val, keep)]),
                    np.concatenate([p[k] for p, k in zip(probs, keep)]))
    return {"threshold": t, "alerts": None if t is None else burden[t][0],
            "bins_alerting": None if t is None else burden[t][1]}, loss


def _export(s: Student, work: Path, name: str, px: np.ndarray) -> tuple[bytes, bytes, dict, np.ndarray]:
    """ONNX (from the weights), MindIR (mindspore.export), and the three-way parity on px."""
    cell = Cell(s)
    cell.set_train(False)
    n = len(s.mean)
    ms.export(cell, Tensor(np.zeros((1, n), np.float32)), file_name=str(work / name), file_format="MINDIR")
    mindir = (work / f"{name}.mindir").read_bytes()
    onnx_bytes = to_onnx(s)
    p_ms = forward(cell, px)
    graph = nn.GraphCell(ms.load(str(work / f"{name}.mindir")))
    p_ir = np.array([graph(Tensor(px[i:i + 1])).asnumpy()[0, 0] for i in range(len(px))], np.float32)
    p_onnx = onnx_predict(session(onnx_bytes), px)
    d = lambda a, b: round(float(np.max(np.abs(a.astype(np.float64) - b.astype(np.float64)))), 9)  # noqa: E731
    parity = {"rows": len(px), "mindspore_vs_mindir": d(p_ms, p_ir), "mindspore_vs_onnx": d(p_ms, p_onnx),
              "mindir_vs_onnx": d(p_ir, p_onnx), "tolerance": PARITY_TOL}
    if max(parity["mindspore_vs_mindir"], parity["mindspore_vs_onnx"], parity["mindir_vs_onnx"]) > PARITY_TOL:
        raise RuntimeError(f"{name}: MindSpore, MindIR and ONNX disagree by more than {PARITY_TOL}: {parity}")
    return mindir, onnx_bytes, parity, p_ms


def _logits(layers, x: np.ndarray) -> np.ndarray:
    """Logits of a trained net on standardised float32 rows."""
    n = x.shape[1]
    return forward(Cell(Student("m", np.zeros(n, np.float32), np.ones(n, np.float32), layers), sigmoid=False), x)


def teacher_gate(yv, gv, z_teacher, const: float, p_old, p_hard, ratio: float) -> tuple[dict, bool]:
    """The teacher gate. Every model is scored by `row_loss` from its own unclipped probability, the
    same function and the same 1e-7 clip for the teacher and for references (a), (b) and (c)."""
    loss_of = lambda p: row_loss(yv, p)  # noqa: E731
    gate = paired_gate(loss_of(_sigmoid(z_teacher)), {"constant": loss_of(np.full(len(yv), const)),
                                                      "old_recipe": loss_of(p_old),
                                                      "hard_label_mlp": loss_of(p_hard)}, gv)
    return gate, all(v["passes"] for v in gate.values()) and ratio >= 10


def _variant(tag: str, temp: bool, train, val, g: Grid, plan: Plan, work: Path, log) -> Result:
    if "base" not in g.student_inputs:
        raise ValueError("reference (b) needs the base inputs")
    xt, yt, mean, std = _prepare(train, "teacher", temp)
    xv, yv, gv = _stack(val, "teacher", temp)
    _standardise(xv, mean, std)
    const = float(yt.mean())
    rl_fits, rl_refs, times = [], {}, {"fits": {}, "references": {}}

    def note(name, kind, inputs, width, wd, f, params, ap):
        """The run log's deterministic record of one fit, and its wall times kept apart."""
        rl_fits.append({"name": name, "kind": kind, "inputs": inputs, "width": width, "weight_decay": wd,
                        "parameters": params, "best_epoch": f.best_epoch,
                        "train_loss_by_epoch": [_round(v) for v in f.train_losses],
                        "val_log_loss_by_epoch": [_round(v) for v in f.val_losses],
                        "final_val_log_loss": _round(min(f.val_losses)), "average_precision": _round(ap)})
        times["fits"][name] = {"seconds": round(sum(f.epoch_seconds), 3),
                               "epoch_seconds": [round(v, 3) for v in f.epoch_seconds]}

    # teacher: one fit per weight decay, the best by validation log loss
    fits = []
    for wd in sorted(g.teacher_decays, reverse=True):
        if wd not in plan.decays:  # a budget cut removed it
            continue
        f = _fit_teacher(xt, yt, g, wd, lambda: plan.epochs, (xv, yv), log, f"fit teacher {tag} decay {wd:g}",
                         plan.first_epoch)
        s = Student("teacher", mean, std, f.layers)
        ap = average_precision(yv, _sigmoid(_logits(f.layers, xv)))
        note(f"{tag} teacher decay {wd:g}", "teacher", "teacher", list(g.teacher_widths), wd, f, s.parameters, ap)
        fits.append({"weight_decay": wd, "parameters": s.parameters, "best_epoch": f.best_epoch,
                     "epochs_run": len(f.val_losses), "exact": min(f.val_losses), "val_log_loss": _round(min(f.val_losses)),
                     "val_log_loss_by_epoch": [_round(v) for v in f.val_losses], "student": s})
    chosen_t = pick(fits, _teacher_key)
    teacher = chosen_t["student"]
    soft = _soft(teacher, xt)
    # the clip above is for the students' targets only: every loss below uses the unclipped probability
    z_val = _logits(teacher.layers, xv)
    teacher_loss = log_loss(yv, _sigmoid(z_val))
    chosen_t["train_log_loss"] = _round(log_loss(yt, _sigmoid(_logits(teacher.layers, xt))))
    ref_d = None
    if plan.reference_d:
        log(f"fit reference (d) {tag}")
        t0 = timeit.default_timer()
        p_d, iters = reference_regularised(xt, yt, xv, yv, g)
        ref_d = {"val_log_loss": _round(log_loss(yv, p_d)), "iterations": iters}
        rl_refs["regularised_gradient_boosting"] = ref_d
        times["references"]["regularised_gradient_boosting"] = round(timeit.default_timer() - t0, 3)
    del xt

    # students on the distilled targets and (reference c) on the hard labels
    distilled, hard, p_old, raw, first = [], [], None, {}, {}
    for kind in g.student_inputs:
        xs, ys, _ = _stack(train, kind, temp)
        xsv, _, _ = _stack(val, kind, temp)
        raw[kind], first[kind] = xs[_sample(len(xs), g.parity_rows)].copy(), xsv[:1].copy()
        if kind == "base":
            log(f"fit reference (b) {tag}")
            t0 = timeit.default_timer()
            p_old, iters_b = reference_old_recipe(xs, ys, xsv, yv, g.hgb_b_iter)
            rl_refs["old_recipe"] = {"val_log_loss": _round(log_loss(yv, p_old)), "iterations": iters_b}
            times["references"]["old_recipe"] = round(timeit.default_timer() - t0, 3)
        m, sd = _moments(xs)
        _standardise(xs, m, sd)
        _standardise(xsv, m, sd)
        for w in g.student_widths:
            for loss, target, bucket, name in (("mse", soft, distilled, "student"), ("bce", ys, hard, "hard-label")):
                f = _fit_student(xs, target, loss, w, g, (xsv, yv), log, f"fit {name} {tag} {kind} width {w}")
                st = Student("mlp", m, sd, f.layers)
                z = _logits(f.layers, xsv)
                stats = _val_stats(yv, z, np.clip(z_val, -LOGIT_CLIP, LOGIT_CLIP), teacher_loss)
                note(f"{tag} {'distilled' if loss == 'mse' else 'hard-label'} {kind} width {w}",
                     "distilled" if loss == "mse" else "hard_label", kind, w, g.student_decay, f, st.parameters,
                     stats["average_precision"])
                bucket.append({"inputs": kind, "width": w, "weight_decay": g.student_decay, "loss": loss,
                               "parameters": st.parameters, "best_epoch": f.best_epoch,
                               "epochs_run": len(f.val_losses),
                               "val_log_loss_by_epoch": [_round(v) for v in f.val_losses],
                               **stats, "student": st, "p": _sigmoid(z)})
        del xs, xsv
    best_d, best_h = pick(distilled, _student_key), pick(hard, _student_key)

    # the teacher gate and the student's own comparisons (patient-level paired bootstrap)
    loss_of = lambda p: row_loss(yv, p)  # noqa: E731
    teacher_params = teacher.parameters
    ratio = teacher_params / best_d["parameters"]
    gate, teacher_passes = teacher_gate(yv, gv, z_val, const, p_old, best_h["p"], ratio)
    stu_gate = paired_gate(loss_of(best_d["p"]), {"constant": loss_of(np.full(len(yv), const)),
                                                  "best_hard_label_mlp": loss_of(best_h["p"])}, gv)
    hard_gate = paired_gate(loss_of(best_h["p"]), {"constant": loss_of(np.full(len(yv), const))}, gv)
    shipped = decide(teacher_passes, stu_gate["constant"]["passes"], hard_gate["constant"]["passes"])
    chosen = {"distilled_student": best_d, "hard_label_mlp": best_h, None: None}[shipped]

    # thresholds, by the matched-burden rule on the validation trials
    chart = chart_burden(val)
    thresholds = {"chart": {"alerts": chart[0][0], "bins_alerting": chart[0][1], "bins": chart[1],
                            "patient_days": _round(chart[2])},
                  "teacher": None, "student": None}

    teacher_cell = Cell(teacher)
    thresholds["teacher"], _ = _burden_record(val, lambda x: forward(teacher_cell, x), temp, "teacher", chart)
    res = Result(tag, temp, None if chosen is None else chosen["inputs"], {})
    exports = None
    onnx_loss = None
    if chosen is not None:
        st = chosen["student"]
        mindir, onnx_bytes, parity, p_ms = _export(st, work, tag, raw[chosen["inputs"]])
        # the shipped artefact is the ONNX file: the student's threshold and alert burden come from its output
        sess = session(onnx_bytes)
        thresholds["student"], onnx_loss = _burden_record(val, lambda x: onnx_predict(sess, x), temp,
                                                          chosen["inputs"], chart)
        res.onnx, res.mindir, res.student = onnx_bytes, mindir, st
        res.parity_x, res.parity_p, res.timing_row = raw[chosen["inputs"]], p_ms, first[chosen["inputs"]]
        exports = {"parity": parity, "onnx_sha256": sha256(onnx_bytes), "onnx_bytes": len(onnx_bytes),
                   "onnx_ops": onnx_ops(onnx_bytes), "weights_sha256": weights_sha256(st)}
    res.teacher = teacher
    res.runlog = {"rows": {"training_rows": len(yt), "validation_rows": len(yv), "training_positives": int((yt == 1).sum()),
                           "validation_positives": int((yv == 1).sum())},
                  "fits": rl_fits, "references": rl_refs}
    res.times = times
    public = lambda c: {k: v for k, v in c.items() if k not in ("student", "p", "exact")}  # noqa: E731
    sc = None if chosen is None else {k: chosen[k] for k in ("inputs", "width", "weight_decay", "loss", "best_epoch",
                                                              "parameters", "val_log_loss")}
    if sc is not None:
        sc["val_log_loss_onnx"] = _round(onnx_loss)  # selection used the MindSpore value above
    res.meta = {
        "tag": tag, "temperature": temp, "training_rows": len(yt), "validation_rows": len(yv),
        "training_positive_rate": _round(const),
        "features_teacher": feature_names(temp, "teacher"),
        "features_student": None if chosen is None else feature_names(temp, chosen["inputs"]),
        "references": {
            "constant": {"val_log_loss": _round(log_loss(yv, np.full(len(yv), const)))},
            "old_recipe": {"val_log_loss": _round(log_loss(yv, p_old))},
            "hard_label_mlp": {k: best_h[k] for k in ("inputs", "width", "val_log_loss", "best_epoch")},
            "regularised_gradient_boosting": ref_d},
        "teacher": {"fits": [public(c) for c in fits], "chosen": public(chosen_t),
                    "weights_sha256": weights_sha256(teacher), "parameters": teacher_params,
                    "val_log_loss": _round(teacher_loss)},
        "grid": {"teacher": [public(c) for c in fits], "distilled": [public(c) for c in distilled],
                 "hard_label": [public(c) for c in hard]},
        "teacher_gate": {**gate, "much_bigger": ratio >= 10, "passes": teacher_passes},
        "student_gate": {**stu_gate, "hard_label_gate_constant": hard_gate["constant"]},
        "shipped": shipped, "distilled": shipped == "distilled_student", "student": sc,
        "parameters": {"teacher": teacher_params, "student": None if chosen is None else chosen["parameters"],
                       "standardisation_constants": None if chosen is None else 2 * len(chosen["student"].mean),
                       "ratio_teacher_to_student": None if chosen is None else _round(teacher_params / chosen["parameters"]),
                       "ratio_teacher_to_largest_distilled_student":
                           _round(teacher_params / max(c["parameters"] for c in distilled))},
        "thresholds": thresholds,
        "weights_sha256": None if chosen is None else exports["weights_sha256"],
        "onnx_sha256": None if exports is None else exports["onnx_sha256"],
        "onnx_bytes": None if exports is None else exports["onnx_bytes"],
        "onnx_ops": None if exports is None else exports["onnx_ops"],
        "parity": None if exports is None else exports["parity"],
        "parity_rows": None if exports is None else exports["parity"]["rows"],
        "parity_max_abs_diff": None if exports is None else max(
            exports["parity"][k] for k in ("mindspore_vs_mindir", "mindspore_vs_onnx", "mindir_vs_onnx")),
    }
    res.exports = None if exports is None else {"mindir_bytes": len(res.mindir), "mindir_sha256": sha256(res.mindir)}
    return res


def build(train: Sequence[TrialFeatures], val: Sequence[TrialFeatures], g: Grid | None = None,
          work: str | os.PathLike | None = None, log: Callable[[str], None] = lambda s: None) -> Built:
    """Both variants (all, no_temp): teacher, students, references, gate, selection, thresholds, exports.
    Trials outside the split are refused. MindIR files are written under `work` (a temporary folder
    by default) and returned as bytes. Wall time is reported in `seconds` and never in the metadata."""
    g = g or Grid()
    _refuse_outside(train, TRAIN_SEEDS)
    _refuse_outside(val, VAL_SEEDS)
    init_runtime()
    plan, results = Plan(g), {}
    t0 = timeit.default_timer()
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(work) if work is not None else Path(tmp)
        folder.mkdir(parents=True, exist_ok=True)
        for tag, temp in (("all", True), ("no_temp", False)):
            results[tag] = _variant(tag, temp, train, val, g, plan, folder, log)
    return Built(results, list(plan.applied), {"projected_total_s": plan.projected,
                                              "build_s": round(timeit.default_timer() - t0, 1)})
