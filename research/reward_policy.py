"""The reward-driven alerting policy (slice B6): six numbers, plain arithmetic, and its reward.

Definitions are in research/README.md and are implemented here exactly. Standard library only.
Synthetic, not validated.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from biosentry.domain.scoring.tier_b.baseline import DEFAULTS, VITALS, baseline_finding

from .alerts import POLICIES, Policy, scan
from .events import windows
from .trials import BIN_MINUTES, Row, Trial, TrialError

NAMES = ("heart_rate", "respiratory_rate", "spo2", "temperature")
LAMBDA = 0.5  # a policy with NEWS2's alert count must catch over half the episodes early to beat silence
P_MAX = 8
THETA_MIN = 0.1
CHART = POLICIES[0]
_BAD = tuple(VITALS[v].bad for v in NAMES)


@dataclass(frozen=True, slots=True)
class Params:
    weights: tuple[float, float, float, float]
    theta: float
    p: int

    def __post_init__(self) -> None:
        if len(self.weights) != 4 or any(not (w >= 0) or w == float("inf") for w in self.weights):
            raise ValueError("weights must be four finite non-negative numbers")
        if not (THETA_MIN <= self.theta < float("inf")):
            raise ValueError(f"theta must be finite and at least {THETA_MIN}")
        if type(self.p) is not int or not 1 <= self.p <= P_MAX:
            raise ValueError(f"p must be an integer from 1 to {P_MAX}")

    def as_dict(self) -> dict:
        return {"weights": dict(zip(NAMES, self.weights)), "theta": self.theta, "p": self.p}

    @classmethod
    def from_dict(cls, d: dict) -> "Params":
        nums = [d["weights"][v] for v in NAMES] + [d["theta"]]
        if any(type(x) not in (int, float) for x in nums):  # no bool, no string number
            raise TypeError("weights and theta must be numbers")
        return cls(tuple(float(x) for x in nums[:4]), float(nums[4]), d["p"])


def generation_zero(temperature: bool = True) -> Params:
    """B4's k = 3.0, p = 4 in this form (README: a superset of it on RANKED bins)."""
    return Params((1.0, 1.0, 1.0, 1.0 if temperature else 0.0), 3.0, 4)


# One stay as (minute, clipped z per vital) for each bin with a row; None marks a NOT RANKED bin.
Series = list[tuple[int, tuple[float, float, float, float] | None]]


def z_series(rows: Mapping[int, Row]) -> Series:
    """Bin t reads bins <= t only. A missing value or a forming baseline gives 0, never a fill."""
    out: Series = []
    if not rows:
        return out
    series: dict[str, list[float | None]] = {v: [] for v in NAMES}
    for minute in range(min(rows), max(rows) + 1, BIN_MINUTES):
        r = rows.get(minute)
        for v in NAMES:
            series[v].append(None if r is None else getattr(r, v))
        if r is None:
            continue
        if not r.ranked:
            out.append((minute, None))
            continue
        z = []
        for v, bad in zip(NAMES, _BAD):
            res = baseline_finding(v, series[v], DEFAULTS)
            z.append(max(0.0, bad * res[1].z) if isinstance(res, tuple) else 0.0)
        out.append((minute, tuple(z)))
    return out


FORMS = ("sum", "max")  # the search configuration's `form` key (README, slice M2)


def states(params: Params, zs: Series, form: str = "sum") -> dict[int, bool]:
    """Alerting when the score is at least theta in p consecutive scored bins; a gap or a NOT
    RANKED bin ends a run and is not alerting. Score: the weighted sum, or (form "max") the
    largest single weighted z, which can say "any one vital"."""
    if form not in FORMS:
        raise ValueError(f"form must be one of {FORMS}")
    w, theta, p = params.weights, params.theta, params.p
    out: dict[int, bool] = {}
    run, previous = 0, None
    for minute, z in zs:
        if previous is not None and minute - previous != BIN_MINUTES:
            run = 0
        previous = minute
        if z is None:
            run = 0
            out[minute] = False
            continue
        if form == "sum":
            s = w[0] * z[0] + w[1] * z[1] + w[2] * z[2] + w[3] * z[3]
        else:
            s = max(w[0] * z[0], w[1] * z[1], w[2] * z[2], w[3] * z[3])
        run = run + 1 if s >= theta else 0
        out[minute] = run >= p
    return out


# shortcut: memo cleared wholesale past 1,000 stays (one trial is about 100); fine for research runs
_MEMO: dict[int, tuple[Mapping[int, Row], Series]] = {}


def _cached(rows: Mapping[int, Row]) -> Series:
    hit = _MEMO.get(id(rows))
    if hit is not None and hit[0] is rows:
        return hit[1]
    if len(_MEMO) > 1000:
        _MEMO.clear()
    zs = z_series(rows)
    _MEMO[id(rows)] = (rows, zs)
    return zs


def policies(name: str, params: Params, form: str = "sum") -> tuple[Policy, Policy]:
    """The policy alone and "NEWS2 chart trigger OR policy", for `research.evaluate`."""
    def alone(rows: Mapping[int, Row]) -> Mapping[int, bool]:
        return states(params, _cached(rows), form)

    def either(rows: Mapping[int, Row]) -> Mapping[int, bool]:
        return {m: s or CHART.alerting(rows[m]) for m, s in alone(rows).items()}

    never = lambda row: False  # noqa: E731  (states are given whole)
    return Policy(name, never, CHART.abstains, alone), Policy(f"news2_or_{name}", never, CHART.abstains, either)


# --- the reward -------------------------------------------------------------------------------

@dataclass
class Prepared:
    """One trial, computed once and reused for every candidate."""

    seed: int
    rows: dict[str, dict[int, Row]]
    zs: dict[str, Series]
    early: dict[str, tuple[float, float]]  # patient -> (episode_start, fully in), evaluated only
    episodes: int
    cohort_rows: int
    shifts: float  # patient-days * 24 / 12
    news2_rate: float  # chart trigger alerts per patient per 12 h
    news2_on: int  # chart trigger alerting bins


def prepare(trial: Trial) -> Prepared:
    rows: dict[str, dict[int, Row]] = {e.patient: {} for e in trial.episodes}
    for r in trial.rows:
        rows[r.patient][r.minute] = r
    wins, _ = windows(trial)
    if len({w.patient for w in wins}) != len(wins):
        raise TrialError(f"{trial.name}: a patient has two evaluated episodes; the reward keys episodes by patient")
    shifts = trial.patient_days * 24 / 12
    alerts = on = 0
    for r in rows.values():
        fired, alerting = scan(CHART, r)
        alerts += len(fired)
        on += len(alerting)
    # No evaluated episode is allowed: E = 0 makes the reward 0 for every policy (the time-in-alert rule still applies).
    if shifts <= 0 or alerts == 0:
        raise TrialError(f"{trial.name}: needs patient-days and NEWS2 alerts for the reward")
    return Prepared(trial.seed, rows, {p: z_series(r) for p, r in rows.items()},
                    {w.patient: (w.start, w.full_in) for w in wins}, len(wins), len(trial.rows),
                    shifts, alerts / shifts, on)


@dataclass(frozen=True)
class Score:
    reward: float
    caught_early: int
    alerts_per_12h: float
    time_in_alert: float
    rejected: bool  # time in alert above the NEWS2 chart trigger's on this trial


def score(params: Params, t: Prepared, form: str = "sum") -> Score:
    caught = alerts = on = 0
    for patient, r in t.rows.items():
        st = states(params, t.zs[patient], form)
        fired, alerting = scan(Policy("", bool, bool, lambda _: st), r)
        alerts += len(fired)
        on += len(alerting)
        w = t.early.get(patient)
        if w is not None:
            caught += any(w[0] <= m <= w[1] for m in alerting)
    rate = alerts / t.shifts
    reward = caught - LAMBDA * (t.episodes / t.news2_rate) * rate
    return Score(reward, caught, rate, on / t.cohort_rows, on > t.news2_on)


def mean_reward(params: Params, trials: list[Prepared], form: str = "sum") -> tuple[float | None, list[Score]]:
    """Mean reward over the set, or None when the candidate is rejected on any trial."""
    scores = [score(params, t, form) for t in trials]
    if any(s.rejected for s in scores):
        return None, scores
    return sum(s.reward for s in scores) / len(scores), scores
