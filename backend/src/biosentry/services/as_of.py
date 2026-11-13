"""NEWS2 as of a past bin, rebuilt from stored inputs (INTEGRATION.md rulings 6 and 17, section 8.4 (b)).

Scores are never stored. For a bin whose END is `end`, the inputs are: the bin's own four stored device
values, and for each charted code (blood pressure, consciousness, oxygen, SpO2 scale) the newest stored
row at or before `end` by (observed_at, id), with NO age limit (the research rule and the live read's rule
for these four). `score_news2` decides scored or not: nothing here scores. An EARLIER bin cannot be rebuilt
exactly, and gives None, when a charted code has no row at or before `end` (never recorded cannot be told
from pruned), or when a nurse typed a device vital in the 12 hours up to `end` (that value is not in the
stored bins). Research code: synthetic data only, not validated.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from biosentry.domain.scoring.tier_a import News2Inputs, News2Result, score_news2
from biosentry.repositories.postgres import CHARTED_VITALS, DEVICE_VITALS, NURSE_VALID, StoredObservation
from biosentry.schemas import Acvpu, Spo2Scale

_SCALES = {1: Spo2Scale.scale_1, 2: Spo2Scale.scale_2}


@dataclass(frozen=True, slots=True)
class Rebuilt:
    result: News2Result
    inputs: News2Inputs


def _newest(rows: Sequence[StoredObservation], code: str, end: datetime) -> StoredObservation | None:
    found = [r for r in rows if r.code == code and r.observed_at <= end]
    return max(found, key=lambda r: (r.observed_at, r.id)) if found else None


def _value(r: StoredObservation | None) -> StoredObservation | None:
    """The row, or None when it says there is no value (an absent reason, or quality missing)."""
    return None if r is None or r.absent_reason is not None or r.quality == "missing" else r


def news2_as_of(
    device: Mapping[str, float | None], rows: Sequence[StoredObservation], end: datetime, *, earlier: bool
) -> Rebuilt | None:
    """The NEWS2 result of the bin ending at `end`, or None when it cannot be rebuilt (earlier bins only).
    `device` holds the bin's four stored values (None: a gap)."""
    newest = {code: _newest(rows, code, end) for code in CHARTED_VITALS}
    if earlier:
        if any(r is None for r in newest.values()):
            return None
        if any(
            r.source == "nurse" and r.code in DEVICE_VITALS and end - NURSE_VALID <= r.observed_at <= end
            for r in rows
        ):
            return None
    bp, level, o2, scale = (_value(newest[c]) for c in CHARTED_VITALS)
    inputs = News2Inputs(
        respiratory_rate=device["respiratory_rate"],
        spo2=device["spo2"],
        spo2_scale=None if scale is None else _SCALES.get(int(scale.num or 0)),
        supplemental_o2=None if o2 is None else bool(o2.num),
        systolic_bp=None if bp is None or bp.num is None else float(int(bp.num)),
        heart_rate=device["heart_rate"],
        consciousness=None if level is None else Acvpu.__members__.get(level.txt or ""),
        temperature=device["temperature"],
    )
    return Rebuilt(score_news2(inputs), inputs)
