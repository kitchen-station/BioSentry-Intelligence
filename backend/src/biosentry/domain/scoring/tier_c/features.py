"""The 35 inputs of the student, variant `all`, from plain series (INTEGRATION.md sections 3, 4.3, 4.5, 5.2).

Port of research/student.py `patient_features` and `stay_features` ("trend" view). Pure: the caller gives each
vital's 15-minute series on one shared grid (None is a gap) and the index t of the current bin; nothing here
reads a database, a clock or a file. Computed in double precision, then rounded once to float32 (ruling 9).
This module does NOT gate: while a baseline forms the z is 0.0 with its flag 1.0, as research does, and the
caller applies the product's gates (ruling 10). Research code: synthetic data only, not validated.
"""

from collections.abc import Mapping, Sequence

from biosentry.domain.scoring.tier_b.baseline import DEFAULTS, baseline_finding
from biosentry.domain.scoring.tier_b.reward_policy import NAMES, bin_z

from .float32 import to_float32

LAG_1H, LAG_4H = 4, 16
N_FEATURES = 35
Series = Mapping[str, Sequence[float | None]]


def signed_z(series: Series, k: int) -> dict[str, float | None]:
    """Each vital's baseline z at bin k from the series up to and including k; None while it forms (or the
    value at k is missing). Signed and not clipped."""
    out: dict[str, float | None] = {}
    for v in NAMES:
        res = baseline_finding(v, series[v][: k + 1], DEFAULTS)
        out[v] = None if not isinstance(res, tuple) else res[1].z
    return out


def formed(series: Series, t: int) -> bool:
    """The baseline of every one of the four vitals has formed at bin t (ruling 10)."""
    return all(z is not None for z in signed_z(series, t).values())


def policy_z(series: Series, k: int) -> tuple[float, float, float, float]:
    """Section 5.2: the clipped z of bin k per vital, 0.0 where the baseline still forms (as research)."""
    return bin_z(signed_z(series, k))


def student_features(
    series: Series, t: int, *, news2_total: int, on_oxygen: bool, consciousness_alert: bool
) -> tuple[float, ...]:
    """The 35 inputs at the scored bin t, each rounded to float32. Order: per vital (heart rate, respiratory
    rate, SpO2, temperature) value, missing, z, z missing; NEWS2 total, on oxygen, consciousness not alert;
    then per vital d1h, d1h missing, d4h, d4h missing. Raises OverflowError beyond the float32 range."""
    z = signed_z(series, t)
    f: list[float] = []
    for v in NAMES:
        x = series[v][t]
        f += [0.0, 1.0] if x is None else [float(x), 0.0]
        f += [0.0, 1.0] if z[v] is None else [z[v], 0.0]  # type: ignore[list-item]
    f += [float(news2_total), float(on_oxygen), float(not consciousness_alert)]
    for v in NAMES:
        x = series[v][t]
        for lag in (LAG_1H, LAG_4H):
            then = series[v][t - lag] if t - lag >= 0 else None
            f += [0.0, 1.0] if then is None or x is None else [x - then, 0.0]
    row = tuple(to_float32(x) for x in f)
    assert len(row) == N_FEATURES
    return row


def baseline_touched(series: Sequence[float | None], dropped: Sequence[bool]) -> bool:
    """Whether a value that `possible()` dropped lies before the end of this vital's baseline window (the
    first `window_bins` valid bins). Research keeps such a value; the grid does not, so the window differs."""
    seen = 0
    for i, x in enumerate(series):
        seen += x is not None
        if seen == DEFAULTS.window_bins:
            return any(dropped[:i])
    return any(dropped)
