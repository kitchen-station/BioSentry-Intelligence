"""Choose the baseline detector's k and p on the tuning seeds (1 to 10) only.

    python -m research.sweep <trial folder>... [--write-params <path>]

The rule is in research/README.md. Held-out trials (seeds 11 to 30) are skipped: they are never
passed to the scorer, so no number here can contain them. Writes nothing without --write-params.
"""

import json
import os
import sys
from dataclasses import dataclass
from fractions import Fraction

from . import baseline_policy
from .alerts import POLICIES
from .evaluate import HELD_OUT, TUNING, Pool, score_trial
from .rescore import check_parity
from .trials import TrialError, load_trial

KS = (1.5, 2.0, 2.5, 3.0, 3.5, 4.0)
PS = (2, 3, 4, 6, 8)
BUDGET = Fraction(5, 4)  # alerts of "NEWS2 OR baseline" over NEWS2 chart trigger alone
RULE = (
    "among settings where NEWS2 OR baseline raises at most 25% more alerts per patient per 12 h "
    "than the NEWS2 chart trigger alone, the largest excess over chance of ALERTING in any bin "
    "on episodes not cut short; ties: fewer alerts, then larger k, then larger p"
)
CHART = POLICIES[0].name


@dataclass(frozen=True)
class Setting:
    k: float
    p: int
    alerts: int  # NEWS2 OR baseline
    excess: Fraction  # any-bin, not cut short, minus chance


def choose(grid: list[Setting], chart_alerts: int) -> Setting | None:
    ok = [s for s in grid if s.alerts <= BUDGET * chart_alerts]
    return min(ok, key=lambda s: (-s.excess, s.alerts, -s.k, -s.p), default=None)


def _frac(n: int, d: int) -> Fraction:
    return Fraction(n, d) if d else Fraction(0)


def excess(hits: int, episodes: int, chance_hits: int, controls: int) -> Fraction:
    return _frac(hits, episodes) - _frac(chance_hits, controls)


def sweep(folders: list[str]) -> tuple[list[dict], Setting | None, dict]:
    trials = []
    for folder in sorted(folders):
        trial = load_trial(folder)
        if trial.seed in HELD_OUT:
            continue
        if trial.seed not in TUNING:
            raise TrialError(f"{trial.name}: seed {trial.seed} is in neither split (1 to 30)")
        check_parity(trial)
        trials.append(trial)
    if not trials:
        raise TrialError("no tuning trial (seeds 1 to 10) given")
    grid = {(k, p): baseline_policy.baseline_policies(k, p, f"_k{k}_p{p}") for k in KS for p in PS}
    policies = (POLICIES[0], *(q for pair in grid.values() for q in pair))
    pool = Pool()
    for trial in trials:
        score_trial(trial, policies, pool)
        baseline_policy.clear_cache()
    days, ev, unc, ctrl = pool.patient_days, pool.evaluated, pool.uncut, pool.controls

    def stats(name: str) -> dict:
        t = pool.tallies[name]
        return {
            "alerts": t.alerts,
            "alerts_per_patient_12h": round(t.alerts / (days * 2), 4),
            "new_all": [t.new, ev],
            "any_uncut": [t.uncut_any, unc],
            "chance_any": [t.ctrl_any, ctrl],
            "excess_any_uncut": round(float(excess(t.uncut_any, unc, t.ctrl_any, ctrl)), 4),
        }

    rows, settings = [], []
    for (k, p), (alone, either) in grid.items():
        o = stats(either.name)
        rows.append({"k": k, "p": p, "news2_or_baseline": o, "baseline_alone": stats(alone.name)})
        settings.append(Setting(k, p, o["alerts"], excess(*o["any_uncut"], *o["chance_any"])))
    chart = stats(CHART)
    ref = {"trials": len(trials), "episodes_evaluated": ev, "not_cut_short": unc,
           "control_windows": ctrl, "patient_days": round(days, 1), "news2_chart_trigger": chart}
    return rows, choose(settings, chart["alerts"]), ref


def _r(pair: list[int]) -> float:
    return pair[0] / pair[1] if pair[1] else 0.0


def render(rows: list[dict], best: Setting | None, ref: dict) -> str:
    c = ref["news2_chart_trigger"]
    out = [
        f"tuning seeds 1 to 10 only: {ref['trials']} trials, {ref['episodes_evaluated']} episodes "
        f"({ref['not_cut_short']} not cut short), {ref['control_windows']} control windows",
        f"NEWS2 chart trigger alone: {c['alerts_per_patient_12h']:.3f} alerts/patient/12h, any-bin "
        f"{c['any_uncut'][0]}/{c['any_uncut'][1]}, chance {c['chance_any'][0]}/{c['chance_any'][1]}, "
        f"excess {c['excess_any_uncut']:+.3f}",
        f"rule: {RULE}",
        "",
        "k    p  | NEWS2 OR baseline: alerts/pt/12h  any-bin(not cut)  new-alert(all)  chance  excess"
        "  budget | baseline alone: alerts/pt/12h  excess",
    ]
    for r in rows:
        o, b = r["news2_or_baseline"], r["baseline_alone"]
        ok = o["alerts"] <= BUDGET * c["alerts"]
        out.append(
            f"{r['k']:<4} {r['p']:<2} | {o['alerts_per_patient_12h']:>18.3f}  "
            f"{_r(o['any_uncut']):>15.3f}  {_r(o['new_all']):>14.3f}  "
            f"{_r(o['chance_any']):>6.3f}  {o['excess_any_uncut']:>+6.3f}  "
            f"{'within' if ok else 'over':<6} | {b['alerts_per_patient_12h']:>15.3f}  "
            f"{b['excess_any_uncut']:>+6.3f}"
        )
    out.append("")
    out.append("chosen: none (no setting is within the alert budget)" if best is None
               else f"chosen: k={best.k}, p={best.p}")
    out.append("synthetic, not validated: tuned on the same simulator that generated the data")
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    path = None
    if "--write-params" in argv:
        i = argv.index("--write-params")
        if i + 1 >= len(argv):
            print("refused: --write-params needs a path", file=sys.stderr)
            return 2
        path = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    try:
        rows, best, ref = sweep(argv)
    except TrialError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(render(rows, best, ref))
    if path and best is None:
        return 1  # nothing chosen: nothing written
    if path:
        params = {"k": best.k, "p": best.p, "chosen_on": "ward-v1 seeds 1-10", "rule": RULE,
                  "grid": rows, "tuning": ref, "synthetic": True, "validated": False}
        tmp = f"{path}.tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(params, f, indent=1)
                f.write("\n")
            os.replace(tmp, path)
        except OSError:
            print("refused: cannot write the params file", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
