"""The distilled vitals student (slice B5): features, labels, teacher, students, ONNX. Research only.

Needs numpy, scikit-learn, onnx and onnxruntime (research/pyproject.toml); nothing else in
research/ imports this module. The definitions are in research/README.md and are implemented
here exactly. Synthetic, not validated.
"""

import hashlib
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
from onnx import TensorProto, helper, numpy_helper
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import Ridge
from sklearn.metrics import average_precision_score, log_loss
from sklearn.neural_network import MLPRegressor

from biosentry.domain.scoring.tier_a import News2Score
from biosentry.domain.scoring.tier_b.baseline import DEFAULTS, baseline_finding

from .alerts import POLICIES, Policy
from .evaluate import HELD_OUT, TUNING, Pool, score_trial
from .rescore import check_parity, score_row
from .trials import BIN_MINUTES, Episode, Row, Trial, TrialError, _int_field, _json, load_trial, show

TRAIN_SEEDS = range(1, 8)
VAL_SEEDS = range(8, 11)
VITALS = ("heart_rate", "respiratory_rate", "spo2", "temperature")
OPSET, IR_VERSION = 13, 7
CLIP = 1e-4  # teacher probabilities are clipped before taking log-odds
PARITY_TOL = 1e-5
THRESHOLDS = tuple(i / 1000 for i in range(1, 10)) + tuple(i / 100 for i in range(1, 100))
CHART = POLICIES[0]

# Internal feature columns: the 19 of the full model, then NEWS2's temperature points (only
# ever used to remove them from the total for the no-temperature model).
_FULL = [f"{v}{s}" for v in VITALS for s in ("", "_missing", "_z", "_z_missing")] + [
    "news2_total", "on_oxygen", "consciousness_not_alert"]
_TEMP_POINTS = len(_FULL)


def feature_names(temp: bool) -> list[str]:
    if temp:
        return list(_FULL)
    return _FULL[:12] + ["news2_total_minus_its_temperature_points", "on_oxygen", "consciousness_not_alert"]


def view(x: np.ndarray, temp: bool) -> np.ndarray:
    """The model's input columns from the internal matrix."""
    if temp:
        return x[:, :_TEMP_POINTS]
    total = x[:, 16:17] - x[:, _TEMP_POINTS:]
    return np.hstack([x[:, :12], total, x[:, 17:19]])


def patient_features(rows: Mapping[int, Row]) -> tuple[list[int], np.ndarray]:
    """One stay: the scored (RANKED) bins and their internal feature rows.

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
    return minutes, np.array(feats, dtype=np.float64).reshape(len(minutes), len(_FULL) + 1)


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


def cohort(trials: Sequence[Trial]) -> tuple[np.ndarray, np.ndarray]:
    """Training rows: scored bins with a label, starting patients (admit minute 0) left out."""
    xs, ys = [np.zeros((0, len(_FULL) + 1))], [np.zeros(0, dtype=np.int8)]
    for trial in trials:
        rows = _by_patient(trial)
        for e in trial.episodes:
            if e.admit_minute == 0:
                continue
            minutes, x = patient_features(rows.get(e.patient, {}))
            y = patient_labels(minutes, e)
            xs.append(x[y >= 0])
            ys.append(y[y >= 0])
    return np.vstack(xs), np.concatenate(ys)


# --- the split, enforced where data is read ---------------------------------------------------

def manifest_seed(folder: str) -> int:
    """Only manifest.json is opened: enough to refuse a held-out folder before its data is read."""
    path = Path(folder)
    if not path.is_dir():
        raise TrialError(f"{show(path.name)}: not a folder")
    try:
        return _int_field(_json(path, "manifest.json"), "seed", "manifest.json", 0, 2**31 - 1)
    except TrialError as e:
        raise TrialError(f"{show(path.name)}: {e}") from None


def load_for_training(folders: Sequence[str]) -> tuple[list[Trial], list[Trial], list[int]]:
    """(seeds 1-7, seeds 8-10, skipped held-out seeds). A held-out folder is never loaded."""
    train: list[Trial] = []
    val: list[Trial] = []
    skipped: list[int] = []
    seen: set[int] = set()
    for folder in sorted(folders):
        seed = manifest_seed(folder)
        if seed in HELD_OUT:
            skipped.append(seed)
            continue
        if seed not in TUNING:
            raise TrialError(f"{show(Path(folder).name)}: seed {seed} is in neither split (1 to 30)")
        if seed in seen:
            raise TrialError(f"{show(Path(folder).name)}: seed {seed} given twice")
        seen.add(seed)
        trial = load_trial(folder)
        if trial.seed != seed:  # the manifest changed between the two reads
            raise TrialError(f"{trial.name}: seed changed while reading")
        check_parity(trial)
        (train if seed in TRAIN_SEEDS else val).append(trial)
    if not train or not val:
        raise TrialError("need at least one trial of seeds 1 to 7 and one of seeds 8 to 10")
    return train, val, sorted(skipped)


def _refuse_outside(trials: Sequence[Trial], split: range) -> None:
    for t in trials:
        if t.seed not in split:
            raise TrialError(f"{t.name}: seed {t.seed} is not in seeds {split.start} to {split.stop - 1}")


# --- models -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Student:
    kind: str
    mean: np.ndarray  # float32, the standardisation constants (inside the ONNX graph)
    std: np.ndarray
    layers: tuple[tuple[np.ndarray, np.ndarray], ...]  # (W, b) float32; ReLU between, sigmoid last

    def forward(self, x: np.ndarray) -> np.ndarray:
        """The numpy forward pass, in float32 and in the ONNX graph's order."""
        h = (x.astype(np.float32) - self.mean) / self.std
        for i, (w, b) in enumerate(self.layers):
            h = h @ w + b
            if i < len(self.layers) - 1:
                h = np.maximum(h, np.float32(0))
        return (np.float32(1) / (np.float32(1) + np.exp(-h)))[:, 0]


def fit_teacher(x: np.ndarray, y: np.ndarray, xv: np.ndarray, yv: np.ndarray) -> HistGradientBoostingClassifier:
    t = HistGradientBoostingClassifier(max_iter=500, early_stopping=True, n_iter_no_change=20,
                                       scoring="loss", random_state=0)
    return t.fit(x, y, X_val=xv, y_val=yv)


def fit_students(x: np.ndarray, soft: np.ndarray) -> dict[str, Student]:
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std[std == 0] = 1.0
    z = (x - mean) / std
    p = np.clip(soft, CLIP, 1 - CLIP)
    target = np.log(p / (1 - p))
    f32 = lambda a: np.asarray(a, dtype=np.float32)  # noqa: E731
    lin = Ridge(alpha=1.0).fit(z, target)
    mlp = MLPRegressor(hidden_layer_sizes=(16,), activation="relu", solver="adam", alpha=1e-4,
                       max_iter=100, random_state=0).fit(z, target)
    m, s = f32(mean), f32(std)
    return {
        "logistic": Student("logistic", m, s, ((f32(lin.coef_).reshape(-1, 1), f32([lin.intercept_])),)),
        "mlp": Student("mlp", m, s, tuple((f32(w), f32(b)) for w, b in zip(mlp.coefs_, mlp.intercepts_))),
    }


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
    """rows of one stay -> {scored minute: probability}; memoised per stay, causal per bin."""

    _features: dict[int, tuple[Mapping[int, Row], list[int], np.ndarray]] = {}

    def __init__(self, predict: Callable[[np.ndarray], np.ndarray], temp: bool):
        self.predict, self.temp = predict, temp
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
            f = (rows, *patient_features(rows))
            Scorer._features[id(rows)] = f
        _, minutes, x = f
        out = dict(zip(minutes, map(float, self.predict(view(x, self.temp))))) if minutes else {}
        self._memo[id(rows)] = (rows, out)
        return out


def prob_policy(name: str, scorer: Scorer, threshold: float, with_chart: bool = False) -> Policy:
    """Alerting when p >= threshold on a scored bin (or, with_chart, when NEWS2 escalates)."""
    def states(rows: Mapping[int, Row]) -> dict[int, bool]:
        p = scorer(rows)
        return {m: (m in p and p[m] >= threshold) or (with_chart and CHART.alerting(rows[m])) for m in rows}

    return Policy(name, lambda row: False, CHART.abstains, states)


def match_threshold(alerts: Mapping[float, int], chart_alerts: int) -> float | None:
    """The smallest threshold whose alerts do not exceed the NEWS2 chart trigger's (same seeds)."""
    return min((t for t, n in alerts.items() if n <= chart_alerts), default=None)


def alerts_by_threshold(trials: Sequence[Trial], scorer: Scorer) -> tuple[dict[float, int], int, float]:
    """(alerts per threshold, NEWS2 chart trigger alerts, patient-days) on the given trials."""
    policies = (CHART, *(prob_policy(f"t{t}", scorer, t) for t in THRESHOLDS))
    pool = Pool()
    for trial in trials:
        score_trial(trial, policies, pool)
    return {t: pool.tallies[f"t{t}"].alerts for t in THRESHOLDS}, pool.tallies[CHART.name].alerts, pool.patient_days


# --- the whole recipe ---------------------------------------------------------------------------

def _metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    return {"average_precision": round(float(average_precision_score(y, p)), 6),
            "log_loss": round(float(log_loss(y, np.clip(p, 1e-7, 1 - 1e-7), labels=[0, 1])), 6)}


def build(train: Sequence[Trial], val: Sequence[Trial]) -> dict[str, dict]:
    """Both variants ("all" and "no_temp"): teacher, both students, the kept student's ONNX bytes,
    thresholds and the numbers behind every choice. Refuses any trial outside its split."""
    _refuse_outside(train, TRAIN_SEEDS)
    _refuse_outside(val, VAL_SEEDS)
    xtr, ytr = cohort(train)
    xva, yva = cohort(val)
    if len(set(ytr.tolist())) < 2 or len(set(yva.tolist())) < 2:
        raise TrialError("training and validation each need positive and negative bins")
    out: dict[str, dict] = {}
    for tag, temp in (("all", True), ("no_temp", False)):
        a, b = view(xtr, temp), view(xva, temp)
        teacher = fit_teacher(a, ytr, b, yva)
        students = fit_students(a, teacher.predict_proba(a)[:, 1])
        val_metrics = {"teacher": _metrics(yva, teacher.predict_proba(b)[:, 1])}
        val_metrics |= {k: _metrics(yva, s.forward(b)) for k, s in students.items()}
        kind = max(students, key=lambda k: (val_metrics[k]["average_precision"], k == "logistic"))
        student = students[kind]
        data = to_onnx(student)
        sess = session(data)
        idx = np.linspace(0, len(a) - 1, min(1000, len(a))).astype(int)
        parity = float(np.max(np.abs(onnx_predict(sess, a[idx]) - student.forward(a[idx]))))
        if not parity <= PARITY_TOL:
            raise TrialError(f"{tag}: ONNX parity {parity} above {PARITY_TOL}")
        Scorer.clear()
        s_alerts, chart_alerts, days = alerts_by_threshold(val, Scorer(lambda x: onnx_predict(sess, x), temp))
        t_alerts, _, _ = alerts_by_threshold(val, Scorer(lambda x: teacher.predict_proba(x)[:, 1], temp))
        Scorer.clear()
        s_t, t_t = match_threshold(s_alerts, chart_alerts), match_threshold(t_alerts, chart_alerts)
        out[tag] = {
            "teacher": teacher, "student": student, "onnx": data, "temp": temp,
            "meta": {
                "features": feature_names(temp),
                "n_features": len(feature_names(temp)),
                "student_kind": kind,
                "validation_seeds_8_10": val_metrics,
                "teacher_iterations": int(teacher.n_iter_),
                "training_rows": int(len(ytr)), "training_positive_rows": int(ytr.sum()),
                "validation_rows": int(len(yva)), "validation_positive_rows": int(yva.sum()),
                "threshold_student": s_t, "threshold_teacher": t_t,
                "tuning_alerts_news2_chart_trigger": chart_alerts,
                "tuning_alerts_student_at_threshold": None if s_t is None else s_alerts[s_t],
                "tuning_alerts_teacher_at_threshold": None if t_t is None else t_alerts[t_t],
                "tuning_patient_days": round(days, 3),
                "parity_rows": int(len(idx)), "parity_max_abs_diff": parity, "parity_tolerance": PARITY_TOL,
                "onnx_sha256": sha256(data), "onnx_bytes": len(data), "onnx_ops": onnx_ops(data),
                "opset": OPSET, "ir_version": IR_VERSION, "input": "x float32 [1, n_features]",
                "output": "p float32 [1, 1]",
            },
        }
    return out
