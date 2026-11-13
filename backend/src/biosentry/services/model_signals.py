"""The three model results for one patient, from stored bins (INTEGRATION.md sections 3 to 8; rulings 1 to 17).

`WardService` calls it on every ward and patient read. The
series come from `scoring.grid()` (one implementation), temperature from its own stored bins (ONE statement
for a set of patients, not in HISTORY_VITALS and in no response), the NEWS2 of the bins the models need from
`as_of.news2_as_of` (ONE more statement). Nothing is stored and nothing is approximated: where an input
cannot be had exactly the result is `NotComputed` with a fixed reason.

THE BIN. `vital_bin.bin_at` of a run posted by the simulator is the END of the 15-minute window: ingest
stores `origin + minute` and the simulator sends the minute of the window's end (ward-sim `sim.ts`, "minute %
WINDOW_MINUTES === 0"). The seed and the migration comment say START; for a posted run they are wrong. The
one place that decides is `bin_window` below. Research code: synthetic data only, not validated.
"""

from collections.abc import Mapping, Sequence
from typing import TypeVar
from dataclasses import dataclass
from datetime import datetime

from biosentry.domain.computed import Computed, NotComputed, Result, Window
from biosentry.domain.scoring.tier_a import News2Abstained, News2Score, inputs_from_vitals, score_news2
from biosentry.domain.scoring.tier_b.reward_policy import NAMES, Policy, alert_states, score
from biosentry.domain.scoring.tier_c.confidence import Check, check_calibrated
from biosentry.domain.scoring.tier_c.features import (
    LAG_1H,
    LAG_4H,
    baseline_touched,
    formed,
    policy_z,
    student_features,
)
from biosentry.domain.scoring.tier_c.plain_mlp import Plain, PlainModelError, forward
from biosentry.repositories.postgres import NURSE_VALID, PostgresStore, StoredObservation
from biosentry.schemas import Provenance, ProvenanceSource, QualityLevel, Tier, UnscoredPatient, VitalHistory

from .as_of import news2_as_of
from .models import WIRED_VARIANT, FileProvenance, LoadedModels
from .scoring import BIN, MAX_BINS, counted, grid

T = TypeVar("T")
NOT_RANKED = "not ranked"
LATEST = "latest bin not scored"  # the row is ranked now, but the last stored bin does not score when rebuilt
NOT_COUNTED = "a vital is not counted"
NO_BINS = "no stored bins"
PAST_CAP = "stay past 672 bins"
NOT_ALIGNED = "vital series not on one grid"
FORMING = "baseline forming"
EARLIER = "earlier bin not stored"
IMPOSSIBLE = "impossible value in a lag or baseline bin"
IMPOSSIBLE_BASELINE = "impossible value in a baseline bin"
FEATURES = "feature out of range"
STUDENT_ID = "tier_c/student_vitals"
CHECK_ID = "tier_c/confidence_check"
POLICY_ID = "tier_b/reward_policy"


def bin_window(bin_at: datetime) -> tuple[datetime, datetime]:
    """(start, end) of the 15-minute window whose stored bin_at is this (see the module note: the END)."""
    return bin_at - BIN, bin_at


@dataclass(frozen=True, slots=True)
class ModelProvenance:
    """What the document's section 8.4 and ruling 8 list, until `Provenance` has fields for it."""

    model_id: str
    variant: str
    path: str  # inside the models folder
    sha256: str
    file_run_id: str  # the manifest record's run id
    sim_run_id: int  # the simulator run the bins belong to
    bin_start: datetime
    bin_end: datetime


@dataclass(frozen=True, slots=True)
class ModelSignals:
    probability: Result[float]
    check: Result[Check]
    policy: Result[bool]
    provenance: Mapping[str, ModelProvenance]  # by model id, for the Computed results only


def _none(reason: str) -> ModelSignals:
    n = NotComputed(reason)
    return ModelSignals(n, n, n, {})


@dataclass(frozen=True, slots=True)
class Prepared:
    series: Mapping[str, Sequence[float | None]]  # four vitals on one grid, from grid()
    dropped: Mapping[str, Sequence[bool]]  # a stored value that possible() dropped
    start: datetime  # stored bin_at of slot 0

    @property
    def last(self) -> int:
        return len(self.series[NAMES[0]]) - 1

    def at(self, k: int) -> datetime:
        return self.start + k * BIN

    def device(self, k: int) -> dict[str, float | None]:
        return {v: self.series[v][k] for v in NAMES}


def prepare_series(
    points: Mapping[str, Sequence[VitalHistory]], admitted_at: datetime | None
) -> Prepared | NotComputed:
    """The four vitals' stored bins on one grid, or why not. No state gates here."""
    built: dict[str, tuple[list[float | None], list[bool], datetime]] = {}
    for v in NAMES:
        pts = points[v]
        bins, first = grid(pts, admitted_at, v)
        if first is None:
            continue
        # A stored value of any kind maps to 1.0, which possible() keeps: what differs from the real grid
        # is exactly the values it dropped.
        marks, _ = grid([VitalHistory.model_construct(time=p.time, value=None if p.value is None else 1.0) for p in pts], admitted_at, v)
        if first != next(p.time for p in pts if admitted_at is None or p.time >= admitted_at):
            return NotComputed(PAST_CAP)  # grid() kept the newest 672 slots
        built[v] = (bins, [m is not None and b is None for m, b in zip(marks, bins)], first)
    if not built:
        return NotComputed(NO_BINS)
    start = min(f for _, _, f in built.values())
    n = max((f - start) // BIN + len(b) for b, _, f in built.values())
    if n > MAX_BINS:
        return NotComputed(PAST_CAP)
    series: dict[str, list[float | None]] = {}
    dropped: dict[str, list[bool]] = {}
    for v in NAMES:
        bins, drop, first = built.get(v, ([], [], start))
        if (first - start) % BIN:
            return NotComputed(NOT_ALIGNED)
        left = (first - start) // BIN
        series[v] = [None] * left + bins + [None] * (n - left - len(bins))
        dropped[v] = [False] * left + drop + [False] * (n - left - len(drop))
    return Prepared(series, dropped, start)


def eligible(state: UnscoredPatient) -> str | None:
    """The reason the models are not computed for this patient now, from the live read; None when they may be."""
    if isinstance(score_news2(inputs_from_vitals(state.vitals)), News2Abstained):
        return NOT_RANKED
    return None if all(counted(state, v) for v in NAMES) else NOT_COUNTED


def prepare(state: UnscoredPatient, temperature: Sequence[VitalHistory]) -> Prepared | NotComputed:
    reason = eligible(state)
    if reason is not None:
        return NotComputed(reason)
    h = state.vital_history
    pts = {"heart_rate": h.heart_rate, "respiratory_rate": h.respiratory_rate, "spo2": h.spo2, "temperature": temperature}
    return prepare_series(pts, state.admitted_at)


def observation_window(prep: Prepared, models: LoadedModels) -> tuple[datetime, datetime]:
    """The stored observations the rebuild needs, per patient: from 12 hours before the end of the earliest
    bin of the policy's window to the end of the current bin."""
    p = models.policy.params.p if isinstance(models.policy, Policy) else 1
    end = bin_window(prep.at(prep.last))[1]
    return end - (p - 1) * BIN - NURSE_VALID, end


def _computed(value: T, model_id: str, tier: Tier, file: FileProvenance, prep: Prepared, run_id: int,
                 synthetic: bool, computed_at: datetime) -> tuple[Computed[T], ModelProvenance]:
    start, end = bin_window(prep.at(prep.last))
    provenance = Provenance(source=ProvenanceSource.model, tier=tier, observed_at=end, computed_at=computed_at,
                            model_id=model_id, synthetic=synthetic, quality=QualityLevel.ok,
                            variant=WIRED_VARIANT, file_sha256=file.sha256, run_id=file.run_id)
    # coverage 1.0: the bin has all four device values, or it would not have been scored.
    window = Window((start - prep.start).total_seconds(), (end - prep.start).total_seconds(), 1.0)
    return (Computed(value, provenance, window),
            ModelProvenance(model_id, WIRED_VARIANT, file.path, file.sha256, file.run_id, run_id, start, end))


def signals(
    prep: Prepared,
    rows: Sequence[StoredObservation],
    models: LoadedModels,
    *,
    run_id: int,
    synthetic: bool,
    computed_at: datetime,
) -> ModelSignals:
    """The three results at the last stored bin. `rows` are this patient's stored observations from
    `observation_window`."""
    t = prep.last
    now = news2_as_of(prep.device(t), rows, bin_window(prep.at(t))[1], earlier=False)
    assert now is not None  # only an earlier bin can be "not stored"
    if not isinstance(now.result, News2Score):
        return _none(LATEST)
    if not formed(prep.series, t):
        return _none(FORMING)
    touched = any(baseline_touched(prep.series[v], prep.dropped[v]) for v in NAMES)
    out = {"p": NotComputed(FEATURES), "c": NotComputed(FEATURES), "a": NotComputed(FEATURES)}
    sources: dict[str, ModelProvenance] = {}

    def done(key: str, value: object, model_id: str, tier: Tier, file: FileProvenance | None) -> None:
        assert file is not None  # a loaded model has its file
        out[key], sources[model_id] = _computed(value, model_id, tier, file, prep, run_id, synthetic, computed_at)

    student: Plain | NotComputed = models.student
    if isinstance(student, NotComputed):
        out["p"] = out["c"] = student
    elif touched or any(prep.dropped[v][t - lag] for v in NAMES for lag in (LAG_1H, LAG_4H)):
        out["p"] = out["c"] = NotComputed(IMPOSSIBLE)
    else:
        try:
            x = student_features(prep.series, t, news2_total=now.result.total,
                                 on_oxygen=bool(now.inputs.supplemental_o2),
                                 consciousness_alert=str(now.inputs.consciousness) == "A")
            p = forward(student, x)
        except (OverflowError, PlainModelError):
            out["p"] = out["c"] = NotComputed(FEATURES)
        else:
            done("p", p, STUDENT_ID, Tier.C, models.student_file)
            cal = models.calibration
            if isinstance(cal, NotComputed):
                out["c"] = cal
            else:
                done("c", check_calibrated(cal, p), CHECK_ID, Tier.C, models.calibration_file)
    alert = _alerting(prep, rows, models.policy, now.result, touched)
    if isinstance(alert, NotComputed):
        out["a"] = alert
    else:
        done("a", alert, POLICY_ID, Tier.B, models.policy_file)
    return ModelSignals(out["p"], out["c"], out["a"], sources)  # type: ignore[arg-type]


def _alerting(
    prep: Prepared, rows: Sequence[StoredObservation], pol: Policy | NotComputed, current: News2Score, touched: bool
) -> bool | NotComputed:
    """Section 5.2 at the last bin: it depends only on bins t - p + 1 to t, each rebuilt on read."""
    if isinstance(pol, NotComputed):
        return pol
    if touched:
        return NotComputed(IMPOSSIBLE_BASELINE)
    t = prep.last
    ks = range(max(0, t - pol.params.p + 1), t + 1)
    rebuilt = {k: news2_as_of(prep.device(k), rows, bin_window(prep.at(k))[1], earlier=True) for k in ks if k < t}
    if any(r is None for r in rebuilt.values()):
        return NotComputed(EARLIER)
    results = {k: r.result for k, r in rebuilt.items() if r is not None} | {t: current}
    scores = [
        (15 * k, score(pol.params, policy_z(prep.series, k)) if isinstance(results[k], News2Score) else None)
        for k in ks
    ]
    return alert_states(pol.params, scores)[-1]


def model_signals(
    state: UnscoredPatient,
    temperature: Sequence[VitalHistory],
    rows: Sequence[StoredObservation],
    models: LoadedModels,
    *,
    run_id: int,
    computed_at: datetime,
) -> ModelSignals:
    """One patient: their read, their stored temperature bins and their stored observations."""
    prep = prepare(state, temperature)
    if isinstance(prep, NotComputed):
        return _none(prep.reason)
    return signals(prep, rows, models, run_id=run_id, synthetic=state.synthetic, computed_at=computed_at)


def ward_model_signals(
    store: PostgresStore, states: Mapping[str, UnscoredPatient], models: LoadedModels
) -> dict[str, ModelSignals]:
    """A ward read's patients: two statements for the whole set, none for a patient who is NOT RANKED
    or whose vitals are not counted, and none when no patient is left."""
    found = {pid: eligible(s) for pid, s in states.items()}
    ok = [pid for pid, reason in found.items() if reason is None]
    temperature = store.temperature_bins(ok) if ok else {}
    prepared = {pid: prepare(states[pid], temperature.get(pid, [])) for pid in ok}
    windows = {pid: observation_window(p, models) for pid, p in prepared.items() if isinstance(p, Prepared)}
    rows = store.observations_between((pid, lo, hi) for pid, (lo, hi) in windows.items())
    now = store.clock()
    out: dict[str, ModelSignals] = {}
    for pid, state in states.items():
        prep = prepared.get(pid)
        if prep is None:
            out[pid] = _none(found[pid] or NOT_RANKED)
        elif isinstance(prep, NotComputed):
            out[pid] = _none(prep.reason)
        else:
            out[pid] = signals(prep, rows.get(pid, []), models, run_id=store.run_id,
                               synthetic=state.synthetic, computed_at=now)
    return out
