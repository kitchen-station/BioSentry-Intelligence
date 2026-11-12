"""NEWS2 on rows that may lack inputs.

Full inputs: the backend scorer (`research.rescore`), which abstains when anything is missing.
Partial: the points of the components that WERE measured, from the backend's own tables, a lower
bound. A missing input scores nothing and is never replaced by a default value.
"""

from biosentry.domain.scoring.tier_a import round_half_up, tables

from ..trials import Row


def _points(bands: tuple[tables.Band, ...], raw: float, places: int) -> int:
    value = round_half_up(raw, places)
    (hit,) = [b for b in bands if (b.low is None or value >= b.low) and (b.high is None or value <= b.high)]
    return hit.score


def components(row: Row) -> dict[str, int]:
    """Points per measured component (scale 1 SpO2). Unmeasured components are absent."""
    out: dict[str, int] = {}
    for name, bands, value, places in (
        ("respiratory_rate", tables.RESPIRATORY_RATE, row.respiratory_rate, 0),
        ("spo2", tables.SPO2_SCALE_1, row.spo2, 0),
        ("systolic_bp", tables.SYSTOLIC_BP, row.systolic, 0),
        ("heart_rate", tables.HEART_RATE, row.heart_rate, 0),
        ("temperature", tables.TEMPERATURE, row.temperature, 1),
    ):
        if value is not None:
            out[name] = _points(bands, value, places)
    if row.on_oxygen is not None:
        out["supplemental_o2"] = tables.SUPPLEMENTAL_O2_SCORE[row.on_oxygen]
    if row.consciousness is not None:
        alert = row.consciousness == "A"
        out["consciousness"] = tables.CONSCIOUSNESS_ALERT_SCORE if alert else tables.CONSCIOUSNESS_OTHER_SCORE
    return out


def partial_total(row: Row) -> int | None:
    """None when nothing was measured."""
    parts = components(row)
    return sum(parts.values()) if parts else None


def partial_chart_trigger(row: Row) -> bool:
    """The chart's trigger on the measured part: total 5 or more, or any single component at 3."""
    parts = components(row)
    return bool(parts) and (sum(parts.values()) >= tables.MEDIUM_FROM
                            or tables.SINGLE_PARAMETER_TRIGGER in parts.values())
