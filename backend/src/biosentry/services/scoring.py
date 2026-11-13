"""One patient's decision, computed from their stored vitals on every read."""

import math
from collections.abc import Sequence
from datetime import datetime, timedelta

from biosentry.domain.arbiter import NotRanked, Ranked, TierBSignal, arbitrate
from biosentry.domain.computed import NotComputed
from biosentry.domain.scoring.tier_a import News2Abstained, inputs_from_vitals, score_news2
from biosentry.domain.scoring.tier_b.baseline import Deviation, baseline_finding
from biosentry.domain.scoring.tier_b.interface import Finding
from biosentry.repositories.postgres import HISTORY_VITALS
from biosentry.schemas import ProvenanceSource, UnscoredPatient, VitalHistory

Decision = Ranked | NotRanked

BIN = timedelta(minutes=15)
# judgement: two bins. A patch silent for longer has no current bin, so its last run is old news.
STALE_AFTER = 2 * BIN
MAX_BINS = 7 * 24 * 4  # 672: 7 days of 15-minute bins
# The same bounds the Tier A scorer applies (domain/scoring/tier_a/news2.py `_usable`, which is
# private to that tier): a physically impossible value is a gap, never a reading. A heart rate or
# respiratory rate of 0 is a real reading and stays one; for SpO2 and temperature 0 is what a
# zero-fill looks like, so it is a gap.
_PHYSICAL_MAX = 1000.0
_SPO2_MAX = 100.0
_ZERO_IS_NOT_A_READING = frozenset({"spo2", "temperature"})


def possible(vital: str, value: float | None) -> float | None:
    """The value, or None when it is missing or physically impossible."""
    if value is None or not math.isfinite(value):
        return None
    below_floor = value <= 0 if vital in _ZERO_IS_NOT_A_READING else value < 0
    if below_floor or value > _PHYSICAL_MAX or (vital == "spo2" and value > _SPO2_MAX):
        return None
    return value


def grid(
    points: Sequence[VitalHistory], admitted_at: datetime | None, vital: str
) -> tuple[list[float | None], datetime | None]:
    """One vital's stored bins on a 15-minute grid from the first bin at or after admission to
    the latest, and the time of the grid's first slot. A slot with no row, a null, a non-finite
    or a physically impossible value is a gap (None): nothing is ever filled."""
    kept = [p for p in points if admitted_at is None or p.time >= admitted_at]
    if not kept:
        return [], None
    first = kept[0].time
    slots = (kept[-1].time - first) // BIN + 1
    # The baseline window then comes from the oldest bins kept, not from admission.
    # shortcut: the cap assumes the default 7-day retention. With BIOSENTRY_RETENTION_DAYS above 7
    # it drops stored admission data that still exists, and a stay longer than the retention
    # loses its admission anchor anyway (the server prunes older bins); upgrade: size the cap
    # from the configured retention, or store the anchor.
    size = min(slots, MAX_BINS)
    offset = slots - size
    bins: list[float | None] = [None] * size
    for p in kept:
        slot = (p.time - first) // BIN - offset
        value = possible(vital, p.value)
        # Bins off the 15-minute grid can share a slot: a value beats a null, and of two values
        # the later one stands.
        if slot >= 0 and (value is not None or bins[slot] is None):
            bins[slot] = value
    return bins, first + offset * BIN


def counted(state: UnscoredPatient, vital: str) -> bool:
    """Whether this vital's own-baseline finding counts. Only while the patch is sampling (the
    simulator writes a device row on change only, so a silent patch keeps its last value), and
    while the live reading is present and from the device: a nurse-typed value has no history
    behind it. The ward row and the baseline route both ask this."""
    live = getattr(state.vitals, vital)
    return (
        state.last_update_s is not None
        and state.last_update_s <= STALE_AFTER.total_seconds()
        and live.data.present
        and live.provenance.source is ProvenanceSource.device
    )


def drift(state: UnscoredPatient) -> tuple[tuple[Finding, Deviation], ...]:
    """Each vital's own-baseline finding, for the vitals that `counted`."""
    found = []
    for vital in HISTORY_VITALS:
        if not counted(state, vital):
            continue
        bins, _ = grid(getattr(state.vital_history, vital), state.admitted_at, vital)
        result = baseline_finding(vital, bins)
        if not isinstance(result, NotComputed):
            found.append(result)
    return tuple(found)


def decide(state: UnscoredPatient) -> Decision:
    """NEWS2, then the arbiter. Pure: nothing is cached, so a recorded value re-ranks at once.
    A NOT RANKED patient gets no finding computed."""
    tier_a = score_news2(inputs_from_vitals(state.vitals))
    ranked = not isinstance(tier_a, News2Abstained)
    return arbitrate(state.id, tier_a, tier_b=TierBSignal(drift(state)) if ranked else None)
