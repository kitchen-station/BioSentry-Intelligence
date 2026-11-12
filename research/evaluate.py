"""How NEWS2 alone performs as an alerting rule, against the simulator's true episodes.

    python -m research.evaluate <trial folder>...

Prints plain deterministic text (no clock, no paths). Exit 2 with one line on any refusal.
"""

import json
import math
import statistics
import sys
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from biosentry.domain.scoring.tier_b.baseline import DEFAULTS, BaselineParams

from . import bootstrap
from .baseline_policy import NO_TEMP, baseline_policies, clear_cache
from .alerts import POLICIES, REARM_BINS, Policy, scan
from .events import TAIL_MINUTES, control_windows, expected_bins, stay_days, windows
from .rescore import check_parity
from .trials import BIN_MINUTES, KINDS, Trial, TrialError, load_trial

TUNING = range(1, 11)
HELD_OUT = range(11, 31)
SHIFT_HOURS = 12
PARAMS_PATH = Path(__file__).parent.parent / "models" / "tier_b" / "baseline" / "params.json"
LAST_LINE = "synthetic, not validated: evaluated on the same simulator that generated the data"


@dataclass
class Tally:
    alerts: int = 0
    outside: int = 0  # alerts that fired outside every evaluated episode window
    bins: int = 0  # cohort rows
    abstained: int = 0  # cohort rows the policy abstains on (NOT RANKED)
    on_bins: int = 0  # cohort rows in which the policy is alerting
    new: int = 0  # episodes with a NEW alert in the window
    anyb: int = 0  # episodes alerting in ANY bin of the window
    uncut_new: int = 0
    uncut_any: int = 0
    start_abstained: int = 0
    ctrl_new: int = 0
    ctrl_any: int = 0
    leads: list[float] = field(default_factory=list)
    kinds: dict[str, list[int]] = field(default_factory=lambda: {k: [0, 0] for k in KINDS})


@dataclass
class Pool:
    trials: int = 0
    patients: int = 0
    patient_days: float = 0.0
    evaluated: int = 0
    uncut: int = 0
    ended_early: int = 0  # the stay or trial ended before the episode was fully in
    no_rows: int = 0
    hypotensive: int = 0
    hypotensive_no_cuff: int = 0
    controls: int = 0
    expected_bins: int = 0
    kind_evaluated: dict[str, int] = field(default_factory=lambda: dict.fromkeys(KINDS, 0))
    dropped: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    tallies: dict[str, Tally] = field(default_factory=dict)
    units: dict[str, list[tuple]] = field(default_factory=lambda: defaultdict(list))


def _any(minutes: list[int], lo: float, hi: float) -> bool:
    i = bisect_left(minutes, lo)
    return i < len(minutes) and minutes[i] <= hi


def score_trial(trial: Trial, policies: tuple[Policy, ...], pool: Pool, tail: int = TAIL_MINUTES) -> None:
    wins, dropped = windows(trial, tail)
    controls = control_windows(trial, wins)
    pool.trials += 1
    pool.patients += len(trial.episodes)
    pool.patient_days += trial.patient_days
    pool.evaluated += len(wins)
    pool.controls += len(controls)
    for reason, n in dropped.items():
        pool.dropped[reason] += n
    by_patient: dict[str, dict[int, object]] = defaultdict(dict)
    for r in trial.rows:
        by_patient[r.patient][r.minute] = r
    for e in trial.episodes:
        pool.expected_bins += expected_bins(e.admit_minute, e.leave_minute, trial.days)
    for w in wins:
        rows = by_patient.get(w.patient, {})
        pool.uncut += not w.cut
        pool.ended_early += w.stay_end < w.full_in
        pool.no_rows += not rows
        pool.kind_evaluated[w.kind] = pool.kind_evaluated.get(w.kind, 0) + 1
        if w.kind == "hypotensive":
            pool.hypotensive += 1
            pool.hypotensive_no_cuff += not any(
                # a cuff counts only once a bin inside the window carries it (NEWS2 scores bins)
                r.bp_minute is not None and w.start <= r.bp_minute <= w.end and r.minute <= w.end
                for r in rows.values()
            )
    window_of = {w.patient: w for w in wins}  # one episode per stay
    for p in policies:
        t = pool.tallies.setdefault(p.name, Tally())
        scanned = {e.patient: scan(p, by_patient.get(e.patient, {})) for e in trial.episodes}
        for e in trial.episodes:
            fired, on = scanned[e.patient]
            w = window_of.get(e.patient)
            counting = [m for m in fired if w and w.start <= m <= w.end]
            new, anyb = bool(counting), bool(w) and _any(on, w.start, w.end)
            t.alerts += len(fired)
            t.outside += len(fired) - len(counting)
            t.on_bins += len(on)
            if w:
                t.new += new
                t.anyb += anyb
                t.uncut_new += new and not w.cut
                t.uncut_any += anyb and not w.cut
                kinds = t.kinds.setdefault(w.kind, [0, 0])  # real data brings its own kinds
                kinds[0] += new
                kinds[1] += anyb
                if new:
                    t.leads.append(w.full_in - counting[0])  # the FIRST counting alert
                m0 = int(-(-w.start // BIN_MINUTES) * BIN_MINUTES)  # the bin that holds the start
                row = by_patient[w.patient].get(m0)
                t.start_abstained += row is None or p.abstains(row)
            pool.units[p.name].append(
                (len(fired), int(bool(w)), int(new), int(anyb), stay_days(e.admit_minute, e.leave_minute, trial.days))
            )
        for patient, start, end in controls:
            fired, on = scanned[patient]
            t.ctrl_new += _any(fired, start, end)
            t.ctrl_any += _any(on, start, end)
        t.bins += len(trial.rows)
        t.abstained += sum(p.abstains(r) for r in trial.rows)


def _ratio(n: int, d: int) -> str:
    return "n/a" if d == 0 else f"{n / d:.3f} ({n}/{d})"


def _excess(n: int, d: int, cn: int, cd: int) -> str:
    return "n/a" if d == 0 or cd == 0 else f"{n / d - cn / cd:+.3f}"


def _interval(iv: tuple[float, float] | None) -> str:
    return "n/a" if iv is None else f"{iv[0]:.3f} to {iv[1]:.3f}"


def _ci(pool: Pool) -> dict:
    ratio = lambda a, b: lambda s: s[a] / s[b] if s[b] else None  # noqa: E731
    stats = {
        "shift": lambda s: s[0] / (s[4] * 24 / SHIFT_HOURS) if s[4] else None,
        "new": ratio(2, 1),
        "any": ratio(3, 1),
    }
    return bootstrap.intervals(pool.units, stats)


def _table(pool: Pool, policies: tuple[Policy, ...]) -> list[str]:
    ci = _ci(pool)
    ev, days = pool.evaluated, pool.patient_days
    cells: dict[str, list[str]] = {}
    for p in policies:
        t = pool.tallies[p.name]
        if len(t.leads) >= 2:
            q1, _, q3 = statistics.quantiles(t.leads, n=4, method="inclusive")
            iqr = f"{q1:.1f} to {q3:.1f}"
        else:
            iqr = "n/a"
        ctrl, unc = pool.controls, pool.uncut
        cells[p.name] = [
            f"{t.alerts / (days * 24 / SHIFT_HOURS):.3f}",
            _interval(ci[p.name, "shift"]),
            f"{100 * t.alerts / days:.1f}",
            _ratio(t.on_bins, t.bins),
            _ratio(t.new, ev),
            _interval(ci[p.name, "new"]),
            _ratio(t.anyb, ev),
            _interval(ci[p.name, "any"]),
            str(t.anyb - t.new),
            _ratio(t.uncut_new, unc),
            _ratio(t.uncut_any, unc),
            _ratio(t.ctrl_new, ctrl),
            _ratio(t.ctrl_any, ctrl),
            _excess(t.new, ev, t.ctrl_new, ctrl),
            _excess(t.uncut_new, unc, t.ctrl_new, ctrl),
            _excess(t.anyb, ev, t.ctrl_any, ctrl),
            _excess(t.uncut_any, unc, t.ctrl_any, ctrl),
            f"{100 * (ev - t.new) / days:.1f}",
            "n/a" if t.new == 0 else f"{t.alerts / t.new:.1f}",
            f"{statistics.median(t.leads):.1f}" if t.leads else "n/a",
            iqr,
            _ratio(t.abstained, t.bins),
            _ratio(pool.expected_bins - t.bins, pool.expected_bins),
            _ratio(t.abstained + pool.expected_bins - t.bins, pool.expected_bins),
            str(t.start_abstained),
            *[_ratio(t.kinds[k][0], pool.kind_evaluated[k]) for k in KINDS],
            *[_ratio(t.kinds[k][1], pool.kind_evaluated[k]) for k in KINDS],
        ]
    labels = [
        "alerts per patient per 12-hour shift",
        "  95% interval (patient-level bootstrap)",
        "alerts per 100 patient-days",
        "time in alert (patient-bins alerting / patient-bins)",
        "episodes with a NEW alert in the window / evaluated",
        "  95% interval (patient-level bootstrap)",
        "episodes ALERTING in any bin of the window / evaluated",
        "  95% interval (patient-level bootstrap)",
        "episodes alerting in the window with no new alert (alert already open before it)",
        "episodes with a NEW alert / episodes not cut short",
        "episodes ALERTING in any bin / episodes not cut short",
        "chance: control windows with a NEW alert / control windows",
        "chance: control windows ALERTING in any bin / control windows",
        "excess over chance, NEW alert, all evaluated episodes",
        "excess over chance, NEW alert, episodes not cut short",
        "excess over chance, ALERTING in any bin, all evaluated episodes",
        "excess over chance, ALERTING in any bin, episodes not cut short",
        "missed episodes (no new alert) per 100 patient-days",
        "alerts per episode with a new alert (number needed to evaluate)",
        "lead time median, minutes (episodes with a new alert)",
        "lead time interquartile range, minutes",
        "abstention: patient-bins NOT RANKED / patient-bins",
        "abstention: bins with no row / bins expected in the stays",
        "abstention: NOT RANKED plus no row / bins expected",
        "episodes whose start fell in a NOT RANKED or no-row bin",
        *[f"new-alert sensitivity, {k} episodes" for k in KINDS],
        *[f"any-bin alerting, {k} episodes" for k in KINDS],
    ]
    width = max(len(x) for x in labels) + 2
    col = 28
    out = ["".ljust(width) + "".join(p.name.ljust(col) for p in policies)]
    for i, label in enumerate(labels):
        out.append(label.ljust(width) + "".join(cells[p.name][i].ljust(col) for p in policies))
    return [line.rstrip() for line in out]


def _sanity(pool: Pool, policies: tuple[Policy, ...]) -> None:
    if pool.evaluated == 0:
        raise TrialError("no episodes evaluated on the held-out seeds")
    for p in policies:
        t = pool.tallies[p.name]
        if t.new == pool.evaluated and t.outside == 0:
            raise TrialError(f"{p.name}: sensitivity 1.0 with no alerts outside episode windows")


MAX_PARAMS_BYTES = 64 * 1024
MAX_P = 96  # one day of 15-minute bins


def _bad(reason: str) -> TrialError:
    return TrialError(f"params.json: {reason}")


def _baseline_params() -> tuple[BaselineParams, str]:
    """The file when it exists (any fault is an error, never a silent fallback), else DEFAULTS."""
    try:
        with open(PARAMS_PATH, "rb") as f:
            data = f.read(MAX_PARAMS_BYTES + 1)
    except FileNotFoundError:
        return DEFAULTS, "the detector's DEFAULTS (no params.json)"
    except OSError:
        raise _bad("cannot be read") from None
    if len(data) > MAX_PARAMS_BYTES:
        raise _bad(f"larger than {MAX_PARAMS_BYTES} bytes")
    try:
        obj = json.loads(data)
    except (ValueError, RecursionError):
        raise _bad("not valid JSON (or nested too deeply)") from None
    if not isinstance(obj, dict):
        raise _bad("not a JSON object")
    k, p = obj.get("k"), obj.get("p")
    if type(k) not in (int, float) or not math.isfinite(k) or k <= 0:
        raise _bad("k must be a finite number above 0")
    if type(p) is not int or not 1 <= p <= MAX_P:
        raise _bad(f"p must be an integer from 1 to {MAX_P}")
    try:
        return BaselineParams(k=float(k), p=p), "chosen on seeds 1-10 (params.json)"
    except ValueError as e:
        raise _bad(str(e)) from None


def run(folders: list[str], policies: tuple[Policy, ...] | None = None) -> str:
    if not folders:
        raise TrialError("no trial folders given")
    params, source = _baseline_params()
    if policies is None:
        policies = (
            POLICIES
            + baseline_policies(params.k, params.p)
            + baseline_policies(params.k, params.p, "_no_temp", NO_TEMP)  # sensitivity check, not tuned
        )
    pool, first, seeds, shapes, parity_rows = Pool(), None, {}, defaultdict(set), 0
    for folder in sorted(folders):
        trial = load_trial(folder)
        if trial.seed in seeds:
            raise TrialError(f"{trial.name}: seed {trial.seed} given twice")
        if trial.seed not in TUNING and trial.seed not in HELD_OUT:
            raise TrialError(f"{trial.name}: seed {trial.seed} is in neither split (1 to 30)")
        seeds[trial.seed] = trial.name
        key = (trial.days, trial.speed, trial.scenario_sha256)
        first = first or key
        if key != first:
            raise TrialError(f"{trial.name}: days, speed or scenario hash differ from the other trials")
        for name, value in (("sim commit", trial.sim_commit), ("node major", trial.node_major)):
            shapes[name].add(value)
        parity_rows += check_parity(trial)
        if trial.seed in HELD_OUT:
            score_trial(trial, policies, pool)
            clear_cache()
    _sanity(pool, policies)

    days, speed, sha = first
    tuning = sorted(s for s in seeds if s in TUNING)
    held = sorted(s for s in seeds if s in HELD_OUT)
    join = lambda xs: ", ".join(str(x) for x in sorted(xs, key=str))  # noqa: E731
    lines = [
        "BioSentry: NEWS2 alone as an alerting rule (offline research, simulated data)",
        "format version: 1",
        f"sim commit: {join(shapes['sim commit'])}",
        f"node major: {join(shapes['node major'])}",
        f"trial length: {days} simulated days",
        f"simulator speed: {speed}x",
        f"scenario sha256: {sha}",
        f"parity: {parity_rows} cohort rows re-scored with the backend NEWS2, 0 mismatches",
        f"tuning seeds (1 to 10), loaded and parity-checked but not scored here (the sweep tunes on them): "
        f"{join(tuning) or 'none'}",
        f"baseline policies: k={params.k}, p={params.p}, from {source}; the *_no_temp rows are a "
        "sensitivity check (temperature left out, same k and p, not tuned)",
        f"held-out seeds (11 to 30), the only ones scored: {join(held)}",
        f"held-out patients: {pool.patients}",
        f"held-out patient-days (summary.json): {pool.patient_days:.1f}",
        "starting patients (admit minute 0) are left out of sensitivity and kept for alert burden;",
        "  one of them has a scripted worsening in the fixture that is not an episode",
        f"episodes evaluated: {pool.evaluated}",
        "episodes dropped: " + "; ".join(f"{r}: {n}" for r, n in sorted(pool.dropped.items())),
        f"  evaluated episodes not cut short (the stay and trial cover the whole window): {pool.uncut}",
        f"  evaluated episodes cut short by the end of the stay or trial (kept): {pool.evaluated - pool.uncut}",
        f"  of those, the stay or trial ended before the episode was fully in: {pool.ended_early}",
        f"evaluated episodes with no cohort rows at all (counted as not detected): {pool.no_rows}",
        f"hypotensive episodes evaluated: {pool.hypotensive}; with no cuff reading carried by a bin inside "
        f"the window (NEWS2 saw no new blood pressure): {pool.hypotensive_no_cuff}",
        f"alert: a policy turns from not alerting to alerting; one open alert per patient; re-arms "
        f"after {REARM_BINS * BIN_MINUTES // 60} hours ({REARM_BINS} bins) not alerting; "
        "a missing bin or NOT RANKED row is not alerting",
        "episode event: starts at episode_start, fully in at start + ramp; the window runs from "
        "episode_start to fully-in + 120 minutes",
        "  NEW alert: an alert fires inside the window. ALERTING in any bin: the policy is alerting "
        "in at least one bin of the window, new or already open",
        "lead time: fully-in minute minus the FIRST new alert in the window; positive is before fully in",
        f"chance control: each evaluated episode's window (same offset from admission, same length) is "
        f"laid on every admitted, non-starting patient of the same trial with no episode whose stay "
        f"covers the whole window; no random draws; control windows: {pool.controls}",
        f"confidence intervals: patient-level bootstrap, {bootstrap.RESAMPLES} resamples, "
        f"SplitMix64 generator seed {bootstrap.SEED}, percentile 2.5 to 97.5; exposure per patient is "
        "the stay inside the trial; chance rates have no interval",
        "",
        *_table(pool, policies),
        "",
        LAST_LINE,
    ]
    return "\n".join(lines) + "\n"


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
