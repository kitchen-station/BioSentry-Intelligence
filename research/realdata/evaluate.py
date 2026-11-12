"""NEWS2 and the per-patient baseline rule on real ICU data, test hospitals only.

    python -m research.realdata.evaluate <dataset folder>

The folder is the eICU-CRD Demo 2.0.1 copy with its SOURCE.md. Plain deterministic text (no clock, no
paths, no patient rows, any count under 5 printed as "<5"). Exit 2 with one line on any refusal.
No model is trained and the baseline's parameters are the ones in models/tier_b/baseline/params.json.
"""

import math
from dataclasses import replace
import statistics
import sys

from biosentry.domain.scoring.tier_a import tables

from .. import bootstrap
from ..alerts import POLICIES, Policy
from ..baseline_policy import baseline_policies
from ..evaluate import Pool, _baseline_params, score_trial
from ..trials import TrialError
from . import news2
from .loader import Cohort, check_test_split, load_cohort
from .source import RealDataError
from .split import TEST_HOSPITALS

SMALL = 5
PARTIAL_NAME = "measured-components sum (NOT NEWS2; lower bound)"
NOTE = ("Excess cannot be compared between rules with different chance rates; as a share of ceiling the order "
        "changes, and the intervals overlap.")
LAST_LINE = "real ICU data (eICU-CRD Demo), open access; small test set; not validated for ward use"
SCORES = tables.MAX_TOTAL + 1
Z_ALPHA, Z_POWER = 1.959964, 0.841621  # two-sided 5% test, 80% power


def sc(n: int) -> str:
    return "<5" if n < SMALL else str(n)


def hidden(n: int, d: int) -> bool:
    """n or its complement d - n is under 5: the count (or its subtraction) would expose a small cell."""
    return n < SMALL or d - n < SMALL


def ratio(n: int, d: int) -> str:
    if d < SMALL:
        return "<5"
    if n < SMALL:
        return f"<5/{d}"
    return f">{d - SMALL}/{d}" if d - n < SMALL else f"{n / d:.3f} ({n}/{d})"


def interval(iv: tuple[float, float] | None, n: int, d: int = 10**9) -> str:
    return "n/a" if iv is None or hidden(n, d) else f"{iv[0]:.3f} to {iv[1]:.3f}"


def excess(n: int, d: int, cn: int, cd: int) -> str:
    return "n/a" if d < SMALL or hidden(n, d) or cd == 0 else f"{n / d - cn / cd:+.3f}"


def ceiling(cn: int, cd: int) -> str:
    return "n/a" if cd == 0 else f"{1 - cn / cd:.3f}"


def share(n: int, d: int, cn: int, cd: int) -> str:
    return "n/a" if d < SMALL or hidden(n, d) or cd == 0 or cn >= cd else f"{(n / d - cn / cd) / (1 - cn / cd):+.2f}"


def auc_from_counts(sums: list[float]) -> float | None:
    """Sums = positives at each score 0..20, then negatives. Ties count one half."""
    pos, neg = sums[:SCORES], sums[SCORES:]
    p, n = sum(pos), sum(neg)
    if not p or not n:
        return None
    below, area = 0.0, 0.0
    for s in range(SCORES):
        area += pos[s] * (below + neg[s] / 2)
        below += neg[s]
    return area / (p * n)


def ap_from_counts(sums: list[float]) -> float | None:
    """Average precision: precision at each distinct score threshold, weighted by the recall step."""
    pos, neg = sums[:SCORES], sums[SCORES:]
    p = sum(pos)
    if not p:
        return None
    tp = fp = ap = 0.0
    for s in range(SCORES - 1, -1, -1):
        if pos[s] + neg[s] == 0:
            continue
        tp, fp = tp + pos[s], fp + neg[s]
        ap += pos[s] / p * tp / (tp + fp)
    return ap


def min_detectable_auc_difference(n_pos: int, n_neg: int, auc: float = 0.75, corr: float = 0.5) -> float:
    """Hanley and McNeil (1982) standard error of one AUC, then the paired difference at 80% power."""
    q1, q2 = auc / (2 - auc), 2 * auc * auc / (1 + auc)
    var = (auc * (1 - auc) + (n_pos - 1) * (q1 - auc * auc) + (n_neg - 1) * (q2 - auc * auc)) / (n_pos * n_neg)
    return (Z_ALPHA + Z_POWER) * math.sqrt(2 * var * (1 - corr))


def histograms(cohort: Cohort, score) -> tuple[list[tuple], int, int, int]:
    """Per stay: positives then negatives per score; plus bins scored, positive bins, stays with one."""
    by_patient: dict[str, list] = {}
    for r in cohort.trial.rows:
        by_patient.setdefault(r.patient, []).append(r)
    units, scored, positive, stays_pos = [], 0, 0, 0
    for e in cohort.trial.episodes:
        h = [0] * (2 * SCORES)
        event = cohort.event_minute[e.patient]
        had = False
        for r in by_patient.get(e.patient, []):
            s = score(r)
            if s is None:
                continue
            is_pos = event is not None and 0 < event - r.minute <= 720
            h[s + (0 if is_pos else SCORES)] += 1
            scored += 1
            positive += is_pos
            had |= is_pos
        stays_pos += had
        units.append(tuple(h))
    return units, scored, positive, stays_pos


def _full_total(r) -> int | None:
    return r.news2 if r.ranked else None


def _f(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def _g(value: float | None, iv, stays_pos: int) -> str:
    if stays_pos < SMALL:
        return "n/a (<5 event stays)"
    return f"{_f(value)}  95% interval {_iv(iv)}"


def _iv(iv: tuple[float, float] | None) -> str:
    return "n/a" if iv is None else f"{iv[0]:.3f} to {iv[1]:.3f}"


def _ci(pool: Pool) -> dict:
    ratio_ = lambda a, b: lambda s: s[a] / s[b] if s[b] else None  # noqa: E731
    return bootstrap.intervals(pool.units, {
        "shift": lambda s: s[0] / (s[4] * 2) if s[4] else None, "new": ratio_(2, 1), "any": ratio_(3, 1)})


def render(cohort: Cohort) -> list[str]:
    trial, counts = cohort.trial, cohort.counts
    k_p, source = _baseline_params()
    base = baseline_policies(k_p.k, k_p.p)
    partial = Policy(PARTIAL_NAME, news2.partial_chart_trigger,
                     lambda r: news2.partial_total(r) is None)
    policies = (*POLICIES, partial, *base)
    pool = Pool()
    score_trial(trial, policies, pool, tail=0)
    n_events = sum(e.kind is not None for e in trial.episodes)
    n_stays = len(trial.episodes)
    out = [
        "Real ICU data: eICU-CRD Demo 2.0.1 (PhysioNet, ODbL). Test hospitals only.",
        f"test hospitals: {len(TEST_HOSPITALS)}; baseline parameters: k {k_p.k}, p {k_p.p} ({source})",
        "",
        "Cohort (aggregates; a count under 5 is shown as <5)",
    ]
    for key in ("not the first stay of the patient", "not adult", "unit stay under 12 hours",
                "vasopressor before hour 6", "comfort or no-CPR death"):
        out.append(f"  left out, {key}: {sc(counts.get(key, 0))}")
    kinds = {k: counts.get(f"events: {k}", 0) for k in ("vasopressor", "death")}
    out += [
        f"  patients (one stay each) kept: {sc(n_stays)}",
        f"  patient-days (stay up to the event or the unit exit): {trial.patient_days:.1f}",
        f"  events: {sc(n_events)} (first vasopressor from hour 6: {sc(kinds['vasopressor'])}; "
        f"unit death: {sc(kinds['death'])})",
        "  events whose 12-hour window starts before admission (window shortened): "
        f"{sc(counts.get('events with fewer than 12 hours of look-back', 0))}",
        f"  15-minute bins: {len(trial.rows)}",
    ]
    rows = trial.rows
    full = sum(r.ranked for r in rows)
    out += ["", f"full NEWS2 inputs available: {100 * full / len(rows):.1f}% of bins "
            f"({full}/{len(rows)}); the other bins are NOT RANKED for NEWS2 (abstain)"]
    for name, get in (("heart rate", "heart_rate"), ("respiratory rate", "respiratory_rate"), ("SpO2", "spo2"),
                      ("systolic BP", "systolic"), ("temperature", "temperature"),
                      ("consciousness", "consciousness"), ("oxygen device", "on_oxygen")):
        have = sum(getattr(r, get) is not None for r in rows)
        out.append(f"  {name} present: {100 * have / len(rows):.1f}%")

    ci = _ci(pool)
    ev, days = pool.evaluated, pool.patient_days
    labels = [
        "alerts per patient per 12-hour shift", "  95% interval (patient-level bootstrap)",
        "time in alert (bins alerting / bins)",
        "event windows with a NEW alert / events", "  95% interval",
        "event windows ALERTING in any bin / events", "  95% interval",
        "chance: control windows with a NEW alert", "chance: control windows ALERTING in any bin",
        "excess over chance, NEW alert", "  ceiling (1 - chance), NEW alert", "  excess as share of ceiling",
        "excess over chance, ALERTING in any bin", "  ceiling (1 - chance), any bin", "  excess as share of ceiling",
        "lead time median, minutes (NEW alerts)", "abstention: bins the policy abstains on / bins (NEWS2 only)",
    ]
    cells = []
    for p in policies:
        t = pool.tallies[p.name]
        cells.append([
            f"{t.alerts / (days * 2):.3f}", interval(ci[p.name, "shift"], t.alerts),
            ratio(t.on_bins, t.bins),
            ratio(t.new, ev), interval(ci[p.name, "new"], t.new, ev),
            ratio(t.anyb, ev), interval(ci[p.name, "any"], t.anyb, ev),
            ratio(t.ctrl_new, pool.controls), ratio(t.ctrl_any, pool.controls),
            excess(t.new, ev, t.ctrl_new, pool.controls), ceiling(t.ctrl_new, pool.controls),
            share(t.new, ev, t.ctrl_new, pool.controls),
            excess(t.anyb, ev, t.ctrl_any, pool.controls), ceiling(t.ctrl_any, pool.controls),
            share(t.anyb, ev, t.ctrl_any, pool.controls),
            f"{statistics.median(t.leads):.0f}" if len(t.leads) >= SMALL else "<5",
            "n/a" if p.states is not None else ratio(t.abstained, t.bins),
        ])
    width, col = max(map(len, labels)) + 2, len(PARTIAL_NAME) + 3
    out += ["", "Alert rule: one open alert per patient, re-armed after 4 hours not alerting. Event window: the 12 "
            f"hours before the event. Chance: the same offsets laid on {pool.controls} windows of stays with no event.", ""]
    out.append("".ljust(width) + "".join(p.name.ljust(col) for p in policies))
    out += [(label.ljust(width) + "".join(c[i].ljust(col) for c in cells)).rstrip() for i, label in enumerate(labels)]
    out += ["", NOTE]
    only = sum(news2.partial_chart_trigger(r) and not news2.partial_chart_trigger(replace(r, consciousness=None))
               for r in rows)
    fired = sum(news2.partial_chart_trigger(r) for r in rows)
    with_c = sum(news2.partial_chart_trigger(r) and r.consciousness == "C" for r in rows)
    out.append(f"Not-alert consciousness is present in {with_c} of the {fired} bins where the measured-components sum "
               f"fires and alone causes the firing in {only} (sedated, ventilated patients): in an ICU close to an "
               "outcome-linked input.")
    out.append("Out-of-range values dropped, not converted: "
               + "; ".join(f"{k.split(': ')[1]} {sc(v)}" for k, v in sorted(counts.items())
                           if k.startswith("dropped out of range")))

    out += ["", "NEWS2 total as a score for 'event within the next 12 hours' (bins; patient-level bootstrap, "
            f"{bootstrap.RESAMPLES} resamples, seed {bootstrap.SEED})"]
    full_h = histograms(cohort, _full_total)
    part_h = histograms(cohort, news2.partial_total)
    cis = bootstrap.intervals({"full": full_h[0], "partial": part_h[0]},
                              {"auroc": auc_from_counts, "auprc": ap_from_counts})
    for label, key, (units, scored, positive, stays_pos) in (
            ("NEWS2 total, full inputs only", "full", full_h),
            (f"{PARTIAL_NAME} total, all bins with any input", "partial", part_h)):
        sums = [sum(c) for c in zip(*units)]
        out += [
            f"  {label}",
            f"    bins scored {sc(scored)}, positive bins {sc(positive)} "
            f"(base rate {_f(positive / scored if scored else None)}), stays with a positive bin {sc(stays_pos)}",
            f"    AUROC {_g(auc_from_counts(sums), cis[key, 'auroc'], stays_pos)}",
            f"    AUPRC {_g(ap_from_counts(sums), cis[key, 'auprc'], stays_pos)}",
        ]
    mdd, mdd0 = (min_detectable_auc_difference(n_events, n_stays - n_events, corr=c) for c in (0.5, 0.0))
    out += ["", f"Minimum detectable AUROC difference ({sc(n_events)} event stays, {n_stays - n_events} others; "
            f"Hanley-McNeil at AUROC 0.75, two-sided 5%, 80% power): {mdd:.3f} paired (assumed correlation 0.5), "
            f"{mdd0:.3f} unpaired. A patient-level approximation.", "",
            f"With {n_events} events and full NEWS2 inputs in {100 * full / len(rows):.1f}% of bins, this run cannot "
            "rank NEWS2, the measured-components sum or the baseline rule against each other; no conclusion can be "
            "drawn.", LAST_LINE]
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m research.realdata.evaluate <dataset folder>")
        return 2
    try:
        check_test_split(argv[0])
        print("\n".join(render(load_cohort(argv[0], TEST_HOSPITALS))))
    except (RealDataError, TrialError) as e:
        print(f"refused: {e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
