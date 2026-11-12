"""The per-patient baseline detector as alert policies (research only).

The backend's `baseline_finding` is called per bin on the bins up to that bin, so a state never
reads a later bin. It needs 24 valid bins before it says anything; until then it is "baseline
forming" and the state is not alerting. A bin with no cohort row has no state (the alert rule
counts it as not alerting). A row whose own value for a vital is missing gives "latest bin
missing" for that vital: no finding, never filled in. Each vital is judged on its own, so a NOT
RANKED row (NEWS2 abstains) can still alert on the vitals it does have.
"""

from collections.abc import Mapping

from biosentry.domain.scoring.tier_b.baseline import DEFAULTS, VITALS, BaselineParams, baseline_finding

from .alerts import POLICIES, Policy
from .trials import BIN_MINUTES, Row

_CHART = POLICIES[0]
NO_TEMP = tuple(v for v in VITALS if v != "temperature")  # the sensitivity check
_MEMO: dict[tuple[int, float, tuple[str, ...]], tuple[Mapping[int, Row], dict[int, int]]] = {}


def clear_cache() -> None:
    _MEMO.clear()


def max_runs(rows: Mapping[int, Row], k: float, vitals: tuple[str, ...] = tuple(VITALS)) -> dict[int, int]:
    """Per cohort bin: the longest run (in bins) past k over the four vitals; 0 if none or forming.

    The run does not depend on p: a vital is significant at bin t exactly when its run is at
    least p, so one pass per k serves every p. Equal to calling `baseline_finding(p=p)` per bin.
    """
    hit = _MEMO.get((id(rows), k, vitals))
    if hit is not None and hit[0] is rows:
        return hit[1]
    params = BaselineParams(k=k, p=1, window_bins=DEFAULTS.window_bins, floors=DEFAULTS.floors)
    out: dict[int, int] = {}
    if rows:
        first, series = min(rows), {v: [] for v in vitals}
        for minute in range(first, max(rows) + 1, BIN_MINUTES):
            r = rows.get(minute)
            for v in vitals:
                series[v].append(None if r is None else getattr(r, v))
            if r is None:
                continue
            best = 0
            for v, bins in series.items():
                if sum(x is not None for x in bins) >= params.window_bins:  # else: forming
                    res = baseline_finding(v, bins, params)
                    if isinstance(res, tuple):
                        best = max(best, res[1].run_bins)
            out[minute] = best
    _MEMO[(id(rows), k, vitals)] = (rows, out)
    return out


def baseline_states(rows: Mapping[int, Row], k: float, p: int, vitals: tuple[str, ...] = tuple(VITALS)) -> dict[int, bool]:
    return {m: run >= p for m, run in max_runs(rows, k, vitals).items()}


def baseline_policies(
    k: float, p: int, tag: str = "", vitals: tuple[str, ...] = tuple(VITALS)
) -> tuple[Policy, Policy]:
    """"Baseline alone" (any vital significant) and "NEWS2 chart trigger OR baseline"."""
    def alone(rows: Mapping[int, Row]) -> Mapping[int, bool]:
        return baseline_states(rows, k, p, vitals)

    def either(rows: Mapping[int, Row]) -> Mapping[int, bool]:
        return {m: s or _CHART.alerting(rows[m]) for m, s in baseline_states(rows, k, p, vitals).items()}

    never = lambda row: False  # noqa: E731  (the detector has no NOT RANKED notion; see README)
    return (
        Policy(f"baseline_alone{tag}", never, _CHART.abstains, alone),
        Policy(f"news2_or_baseline{tag}", never, _CHART.abstains, either),
    )
