"""The analysis of held-out set 2, fixed before the set was read (research/README.md, "Held-out set 2").

    python -m research.heldout2 analyse <held-out JSON>... --out <analysis.json>

Reads one or more held-out JSON files made with `--held-out-set 2` (each carries per-trial counts), joins
them, and computes the contract's numbers. The unit is the trial. A bootstrap draws the trials with
replacement, 2,000 times (SplitMix64, seed 20261114: the generator of research/bootstrap.py), and every
ratio is recomputed from the summed per-trial counts of the resample.

The percentile rule: m resamples that gave a number are sorted; with a tail t on each side (1/40 for the
95% interval; 1/120 for the 98.33% interval, which is 95% split by Bonferroni over the three primary
comparisons) the interval runs from the value at index floor(t m) to the value at index floor((1 - t) m) - 1
(0-based; the same rule as research/bootstrap.py, whose 95% interval is positions int(.025 n) and
int(.975 n) - 1). For m = 2000 that is indexes 50 and 1949 at 95%, and 16 and 1982 at 98.33%. A resample
whose ratio has a zero denominator gives no number and is skipped.

A primary verdict is strict: "a gain beyond chance is shown" only when the lower end of the 98.33% interval
is above 0, "a loss is shown" only when its upper end is below 0, else "no gain beyond chance is shown".
Prints plain deterministic text (no clock, no path) and writes the same numbers as JSON. Exit 2 with one line
on any refusal. Standard library only (and the repository's own bootstrap and file writer).
"""

import json
import sys
from fractions import Fraction

from .bootstrap import SplitMix64
from .evaluate import check_json_path, write_json
from .trials import KINDS, TrialError

SEED = 20261114
RESAMPLES = 2000
TAILS = {"95": Fraction(1, 40), "98.33": Fraction(1, 120)}  # mass in each tail
LEVEL_NAMES = {"95": "95%", "98.33": "98.33%"}
FORMAT = "biosentry-held-out-2-analysis/1"
NEWS2 = "news2_chart_trigger"
GAIN, LOSS, NONE = "a gain beyond chance is shown", "a loss is shown", "no gain beyond chance is shown"
PRIMARY = (
    ("the student (with temperature)", "news2_or_student"),
    ("the student with the confidence check (with temperature)", "news2_or_student_checked"),
    ("the reward policy, final (with temperature)", "news2_or_reward_final"),
)
SECONDARY_OR = (
    ("the student, no temperature", "news2_or_student_no_temp"),
    ("the student with the confidence check, no temperature", "news2_or_student_checked_no_temp"),
    ("the reward policy, final, no temperature", "news2_or_reward_final_no_temp"),
)
ALONE = (
    ("the student", "student"), ("the student with the confidence check", "student_checked"),
    ("the reward policy, final", "reward_final"), ("the student, no temperature", "student_no_temp"),
    ("the student with the confidence check, no temperature", "student_checked_no_temp"),
    ("the reward policy, final, no temperature", "reward_final_no_temp"),
)
COUNTS = ("alerts", "shifts", "bins", "bins_alerting", "episodes", "any_bin", "new_alert", "episodes_not_cut_short",
          "any_bin_not_cut_short", "new_alert_not_cut_short", "control_windows", "chance_any", "chance_new")
MAX_BYTES = 256 * 1024 * 1024


# --- the pure core ------------------------------------------------------------------------------

def percentile(values: list[float], tail: Fraction) -> tuple[float, float] | None:
    """The interval of the module docstring; None when there is no value."""
    m = len(values)
    if m == 0:
        return None
    v = sorted(values)
    lo = int(tail * m // 1)
    hi = max(int((1 - tail) * m // 1) - 1, lo)
    return v[lo], v[hi]


def verdict(interval: tuple[float, float] | None) -> str:
    """Strict: a bound at exactly 0 is neither a gain nor a loss."""
    if interval is None:
        return NONE
    if interval[0] > 0:
        return GAIN
    if interval[1] < 0:
        return LOSS
    return NONE


def _rate(s: dict, rule: str, num: str, den: str) -> float | None:
    d = s[rule, den]
    return None if d == 0 else s[rule, num] / d


def _excess(s: dict, rule: str, unc: bool = True) -> float | None:
    suffix, ep = ("_not_cut_short", "episodes_not_cut_short") if unc else ("", "episodes")
    hit, chance = _rate(s, rule, "any_bin" + suffix, ep), _rate(s, rule, "chance_any", "control_windows")
    return None if hit is None or chance is None else hit - chance


def _diff(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


def _gain(s: dict, x: str) -> float | None:
    """[any-bin rate(x) - chance(x)] - [any-bin rate(NEWS2) - chance(NEWS2)], episodes not cut short."""
    return _diff(_excess(s, x), _excess(s, NEWS2))


def _alert_ratio(s: dict, x: str) -> float | None:
    """Alerts per patient per 12 h of x over NEWS2's."""
    a, b = _rate(s, x, "alerts", "shifts"), _rate(s, NEWS2, "alerts", "shifts")
    return None if a is None or b is None or b == 0 else a / b


def _stats(x: str, alone: bool) -> dict:
    """The statistics of one comparison, each a function of the summed counts."""
    out = {"alert_ratio": lambda s: _alert_ratio(s, x)}
    if alone:
        out["any_bin_rate_difference"] = lambda s: _diff(_rate(s, x, "any_bin", "episodes"), _rate(s, NEWS2, "any_bin", "episodes"))
        out["chance_difference"] = lambda s: _diff(_rate(s, x, "chance_any", "control_windows"), _rate(s, NEWS2, "chance_any", "control_windows"))
        out["time_in_alert_difference"] = lambda s: _diff(_rate(s, x, "bins_alerting", "bins"), _rate(s, NEWS2, "bins_alerting", "bins"))
    else:
        out["gain"] = lambda s: _gain(s, x)
    return out


def bootstrap(columns: dict, stats: dict, n: int, resamples: int = RESAMPLES, seed: int = SEED) -> dict:
    """{stat name: {"estimate": value on all trials, "95": interval, "98.33": interval}}.
    `columns[(rule, count name)]` is the list of per-trial values (n trials); `stats[name]` maps the summed
    columns to a number or None."""
    keys = list(columns)
    full = {k: sum(columns[k]) for k in keys}
    values: dict[str, list[float]] = {name: [] for name in stats}
    rng = SplitMix64(seed)
    for _ in range(resamples):
        idx = [rng.next() % n for _ in range(n)]
        sums = {k: sum(map(columns[k].__getitem__, idx)) for k in keys}
        for name, fn in stats.items():
            v = fn(sums)
            if v is not None:
                values[name].append(v)
    return {name: {"estimate": fn(full), **{lvl: percentile(values[name], t) for lvl, t in TAILS.items()}}
            for name, fn in stats.items()}


# --- reading the files --------------------------------------------------------------------------

def _load(path: str) -> dict:
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_BYTES + 1)
    except OSError:
        raise TrialError("a held-out file cannot be read") from None
    if len(data) > MAX_BYTES:
        raise TrialError("a held-out file is too large")
    try:
        obj = json.loads(data)
    except (ValueError, RecursionError):
        raise TrialError("a held-out file is not valid JSON") from None
    return obj


def _check(f: dict) -> None:
    if not isinstance(f, dict) or not isinstance(f.get("context"), dict) or not isinstance(f.get("rules"), list):
        raise TrialError("a held-out file has no context or rules")
    if f["context"].get("held_out_set") != 2:
        raise TrialError("a held-out file is not from held-out set 2 (context.held_out_set is not 2)")
    if not isinstance(f.get("trials"), list) or not f["trials"]:
        raise TrialError("a held-out file has no per-trial counts")


def _num(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        raise TrialError(f"per-trial count {what} is not a non-negative number")
    return v


def join(files: list[dict]) -> tuple[list[int], dict[str, dict[str, list[float]]], dict[str, dict]]:
    """(seeds, per-rule per-count lists over the trials, the pooled rule objects). The files must agree on
    the trials and seeds; a rule in two files must have the same per-trial counts."""
    seeds = None
    columns: dict[str, dict[str, list[float]]] = {}
    pooled: dict[str, dict] = {}
    for f in files:
        _check(f)
        ctx = f["context"]
        these = [t.get("seed") if isinstance(t, dict) else None for t in f["trials"]]
        if ctx.get("trials") != len(these) or ctx.get("held_out_seeds") != these or these != sorted(set(these)) \
                or any(type(s) is not int for s in these):
            raise TrialError("a held-out file's per-trial counts do not match its seeds")
        if seeds is not None and these != seeds:
            raise TrialError("the held-out files disagree on the trials or seeds")
        seeds = these
        file_cols: dict[str, dict[str, list[float]]] = {}
        for rule in f["rules"]:
            rid = rule.get("id") if isinstance(rule, dict) else None
            if not isinstance(rid, str):
                raise TrialError("a held-out file has a rule without an id")
            cols: dict[str, list[float]] = {c: [] for c in COUNTS}
            kinds = {k: {"evaluated": [], "any_bin": []} for k in KINDS}
            for t in f["trials"]:
                c = t["rules"].get(rid) if isinstance(t.get("rules"), dict) else None
                if not isinstance(c, dict) or any(k not in c for k in COUNTS):
                    raise TrialError(f"rule {rid}: a trial has no counts for it")
                for k in COUNTS:
                    cols[k].append(_num(c[k], f"{rid}.{k}"))
                for k in KINDS:
                    kc = c.get("kinds", {}).get(k, {})
                    for part in ("evaluated", "any_bin"):
                        kinds[k][part].append(_num(kc.get(part, 0), f"{rid}.kinds.{k}"))
            cols["kinds"] = kinds  # type: ignore[assignment]
            file_cols[rid] = cols
            pooled.setdefault(rid, rule)
        for rid, cols in file_cols.items():
            if rid in columns and columns[rid] != cols:
                raise TrialError(f"rule {rid} has different per-trial counts in two files")
            columns[rid] = cols
    return seeds or [], columns, pooled


# --- the report ---------------------------------------------------------------------------------

def _ci(r: dict, level: str, places: int = 4, sign: bool = False) -> str:
    iv = r[level]
    f = f"{{:{'+' if sign else ''}.{places}f}}"
    return "n/a" if iv is None else f"{f.format(iv[0])} to {f.format(iv[1])}"


def _v(x, places: int = 4, sign: bool = False) -> str:
    return "n/a" if x is None else f"{x:{'+' if sign else ''}.{places}f}"


def analyse(files: list[dict], resamples: int = RESAMPLES, seed: int = SEED) -> tuple[dict, str]:
    """(the JSON object, the text report)."""
    seeds, cols, pooled = join(files)
    n = len(seeds)
    need = [NEWS2] + [x for _, x in PRIMARY + SECONDARY_OR + ALONE]
    missing = [r for r in need if r not in cols]
    if missing:
        raise TrialError("rules needed for the comparisons are missing: " + ", ".join(missing))
    comparisons = [(label, x, False) for label, x in PRIMARY]
    comparisons += [(label, x, False) for label, x in SECONDARY_OR]
    comparisons += [(label, x, True) for label, x in ALONE]
    stats, used = {}, {NEWS2}
    for label, x, alone in comparisons:
        used.add(x)
        for name, fn in _stats(x, alone).items():
            stats[f"{x}/{name}"] = fn
    columns = {(r, c): cols[r][c] for r in used for c in COUNTS}
    res = bootstrap(columns, stats, n, resamples, seed)
    full = {k: sum(v) for k, v in columns.items()}

    def pool(r: str) -> dict:
        return {"any_bin_not_cut_short": _rate(full, r, "any_bin_not_cut_short", "episodes_not_cut_short"),
                "chance_any": _rate(full, r, "chance_any", "control_windows"),
                "alerts_per_patient_per_12h": _rate(full, r, "alerts", "shifts")}

    def comp(label: str, x: str, alone: bool) -> dict:
        d = {"id": x, "label": label,
             **{name: {"estimate": res[f"{x}/{name}"]["estimate"],
                       **{f"ci_{lvl}": res[f"{x}/{name}"][lvl] for lvl in TAILS}}
                for name in _stats(x, alone)}}
        return d

    primary = [{**comp(label, x, False), "verdict": verdict(res[f"{x}/gain"]["98.33"])} for label, x in PRIMARY]
    secondary_or = [comp(label, x, False) for label, x in SECONDARY_OR]
    secondary_alone = [comp(label, x, True) for label, x in ALONE]
    sums_kinds = {r: {k: {part: sum(cols[r]["kinds"][k][part]) for part in ("evaluated", "any_bin")} for k in KINDS}
                  for r in sorted(used | {x for _, x in PRIMARY})}
    leads = {r: pooled[r].get("lead") for r in sorted(used) if r in pooled}
    obj = {"format": FORMAT, "trials": n, "seeds": seeds,
           "bootstrap": {"unit": "trial", "resamples": resamples, "generator": "SplitMix64", "generator_seed": seed,
                         "tail_per_side": {k: str(v) for k, v in TAILS.items()},
                         "index_rule": "sorted values, indexes floor(t m) and floor((1 - t) m) - 1"},
           "news2": NEWS2, "pooled": {r: pool(r) for r in sorted(used)}, "primary": primary,
           "secondary_or_news2": secondary_or, "secondary_alone": secondary_alone,
           "per_kind_any_bin": sums_kinds, "median_lead": leads}

    lines = [
        "BioSentry: held-out set 2, the declared analysis (offline research, simulated data)",
        f"trials: {n}, seeds {seeds[0]} to {seeds[-1]}; unit of resampling: the trial",
        f"bootstrap: {resamples} resamples of the trials with replacement, SplitMix64 generator seed {seed}, "
        "percentile interval; every ratio is recomputed from the summed per-trial counts of each resample",
        "interval rule: tail t per side (95%: 1/40; 98.33%: 1/120, Bonferroni for three comparisons); sorted "
        "values, indexes floor(t m) and floor((1 - t) m) - 1",
        "gain in excess over chance = [any-bin rate(NEWS2 or X) - chance any-bin(NEWS2 or X)] - [any-bin rate(NEWS2) - "
        "chance any-bin(NEWS2)], episodes not cut short; NEWS2 is the chart trigger",
        "verdict: a gain beyond chance is shown only if the lower end of the 98.33% interval is above 0; a loss is "
        "shown only if its upper end is below 0",
        "alert ratio: alerts per patient per 12 hours of the rule over NEWS2's",
        "",
        "PRIMARY comparisons (NEWS2 or X against NEWS2 alone)",
    ]
    for i, d in enumerate(primary, 1):
        g, a, x = d["gain"], d["alert_ratio"], d["id"]
        lines += [
            f"{i}. {d['label']}: {x}",
            f"   gain in excess over chance {_v(g['estimate'], sign=True)}",
            f"   95% interval {_ci(g, 'ci_95', sign=True)}; 98.33% interval {_ci(g, 'ci_98.33', sign=True)}",
            f"   verdict: {d['verdict']}",
            f"   alert ratio {_v(a['estimate'], 3)}: 95% interval {_ci(a, 'ci_95', 3)}; 98.33% interval {_ci(a, 'ci_98.33', 3)}",
        ]
    lines += ["", "SECONDARY (no verdict word)"]
    for d in secondary_or:
        g, a = d["gain"], d["alert_ratio"]
        lines += [f"NEWS2 or {d['label']}: {d['id']}",
                  f"   gain in excess over chance {_v(g['estimate'], sign=True)}, 95% interval {_ci(g, 'ci_95', sign=True)}; "
                  f"alert ratio {_v(a['estimate'], 3)}, 95% interval {_ci(a, 'ci_95', 3)}"]
    for d in secondary_alone:
        parts = []
        for name, label, places in (("any_bin_rate_difference", "any-bin rate difference, all evaluated episodes", 4),
                                    ("chance_difference", "chance difference", 4), ("alert_ratio", "alert ratio", 3),
                                    ("time_in_alert_difference", "time in alert difference", 4)):
            r = d[name]
            parts.append(f"{label} {_v(r['estimate'], places, name != 'alert_ratio')}, 95% interval "
                         f"{_ci(r, 'ci_95', places, name != 'alert_ratio')}")
        lines += [f"{d['label']} alone against NEWS2: {d['id']}", *[f"   {p}" for p in parts]]
    lines += ["", "pooled any-bin rate (episodes not cut short), chance any-bin, alerts per patient per 12 h, median lead "
              "(median lead: pooled, no interval)"]
    for r in sorted(used):
        p, lead = obj["pooled"][r], (leads.get(r) or {})
        lines.append(f"   {r}: {_v(p['any_bin_not_cut_short'])}, {_v(p['chance_any'])}, "
                     f"{_v(p['alerts_per_patient_per_12h'], 3)}, {_v(lead.get('median'), 1)} min (n {lead.get('n', 'n/a')})")
    lines += ["per-kind any-bin counts (alerting in any bin / evaluated), pooled"]
    for r, ks in sums_kinds.items():
        lines.append(f"   {r}: " + "; ".join(f"{k} {int(v['any_bin'])}/{int(v['evaluated'])}" for k, v in ks.items()))
    lines += ["", "synthetic, not validated: a gain shown is a statement about this simulator only"]
    return obj, "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    try:
        if len(argv) < 4 or argv[0] != "analyse" or argv.count("--out") != 1 or argv[-2] != "--out":
            raise TrialError("usage: analyse <held-out JSON>... --out <analysis.json>")
        paths, out = argv[1:-2], argv[-1]
        check_json_path(out)
        obj, text = analyse([_load(p) for p in paths])
        write_json(out, obj)
    except TrialError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
