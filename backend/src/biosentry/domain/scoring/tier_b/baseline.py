"""Tier B: change from the patient's own baseline, learned without labels.

Per device vital, the baseline is the median and spread of the first `window_bins` valid
15-minute bins after admission, then frozen: anchored, not rolling, so a slow drift is never
absorbed into "normal". A gap (None or a non-finite value) is skipped, never filled, and
breaks a run: it is never evidence either way (I1). A run may reach back into the baseline
window itself: a deviation inside the first hours is still a deviation from the patient's own
start. Re-anchoring after a patch is re-applied is not done here; the caller slices `bins`. Never looks at NEWS2, never ranks, never
alerts; not wired to the arbiter. Synthetic, not validated.
"""

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import islice
from types import MappingProxyType
from typing import TypeGuard

from biosentry.schemas import FindingDirection, ProvenanceSource

from ...computed import NotComputed
from .interface import Finding

MAD_TO_SD = 1.4826  # makes the MAD a consistent SD estimate under normality


@dataclass(frozen=True, slots=True)
class _Vital:
    label: str
    unit: str
    bad: int  # +1 when rising is clinically bad, -1 when falling is
    pathway: str


VITALS: Mapping[str, _Vital] = MappingProxyType({
    "heart_rate": _Vital("Heart rate", "bpm", +1, "circulatory"),
    "respiratory_rate": _Vital("Respiratory rate", "/min", +1, "respiratory"),
    "spo2": _Vital("SpO2", "%", -1, "respiratory"),
    "temperature": _Vital("Temperature", "°C", +1, "temperature"),
})


@dataclass(frozen=True, slots=True)
class BaselineParams:
    # shortcut: 6 h window is judgement (24 h would leave the first day bare), tune in the B4 sweep
    window_bins: int = 24
    # shortcut: k is judgement from the spec's worked example, tune in the B4 sweep
    k: float = 2.0
    # shortcut: p is judgement (45 min of persistence), tune in the B4 sweep
    p: int = 3
    # shortcut: floors are judgement, stop a very stable patient alarming on noise; B4 sweep
    floors: Mapping[str, float] = field(
        default_factory=lambda: {
            "heart_rate": 2.0,
            "respiratory_rate": 1.0,
            "spo2": 1.0,
            "temperature": 0.1,
        }
    )

    def __post_init__(self) -> None:
        if self.window_bins < 1:
            raise ValueError("window_bins must be at least 1")
        if self.p < 1:
            raise ValueError("p must be at least 1")
        if not (math.isfinite(self.k) and self.k > 0):
            raise ValueError("k must be positive and finite")
        for name in VITALS:
            floor = self.floors.get(name, math.nan)
            if not (math.isfinite(floor) and floor > 0):
                raise ValueError("floors must give a positive, finite floor for every vital")
        # A private read-only copy: no caller can change a shared default through `floors`.
        object.__setattr__(self, "floors", MappingProxyType(dict(self.floors)))


@dataclass(frozen=True, slots=True)
class Deviation:
    """The numbers behind a baseline `Finding`, which carries only a formatted value."""

    vital: str
    value: float  # latest bin, own units
    baseline_median: float
    delta: float  # value - baseline_median, own units
    z: float
    run_bins: int  # consecutive valid bins, ending at the latest, past k in the bad direction
    since_bin: int | None  # index from admission where that run starts; None when no run


DEFAULTS = BaselineParams()


def _valid(x: float | None) -> TypeGuard[float]:
    # A bool is not a reading (True would count as 1).
    return x is not None and not isinstance(x, bool) and math.isfinite(x)


def baseline_finding(
    vital: str, bins: Sequence[float | None], params: BaselineParams = DEFAULTS
) -> tuple[Finding, Deviation] | NotComputed:
    """One vital's 15-min bins since admission (None = gap) -> finding, or why not."""
    spec = VITALS.get(vital)
    if spec is None:
        raise ValueError("unknown vital")  # fixed text: never echo caller input into a log
    window = list(islice((x for x in bins if _valid(x)), params.window_bins))
    if len(window) < params.window_bins:
        return NotComputed(f"baseline forming, {len(window)} of {params.window_bins} bins")
    value = bins[-1]
    if not _valid(value):
        return NotComputed("latest bin missing")
    median = statistics.median(window)
    mad = statistics.median([abs(x - median) for x in window])
    spread = max(MAD_TO_SD * mad, params.floors[vital])

    def z_of(x: float) -> float:
        return (x - median) / spread

    run = 0
    for x in reversed(bins):
        if not _valid(x) or spec.bad * z_of(x) < params.k:
            break
        run += 1
    delta = value - median
    z = z_of(value)
    direction = (
        FindingDirection.rising
        if delta > 0
        else FindingDirection.falling
        if delta < 0
        else FindingDirection.stable
    )
    finding = Finding(
        id=f"baseline/{vital}",
        label=f"{spec.label} vs own baseline",
        value=f"{delta:+.1f} {spec.unit} (median {median:.1f}, z {z:+.1f}, {run} bins)",
        direction=direction,
        source=ProvenanceSource.derived,
        significant=run >= params.p,
        pathway=spec.pathway,
    )
    since = len(bins) - run if run else None
    return finding, Deviation(vital, value, median, delta, z, run, since)
