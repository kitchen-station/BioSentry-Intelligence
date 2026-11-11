"""A patient's change from their own baseline, for display. Read-only: it ranks nothing, feeds
no arbiter and stores nothing. Who may see a patient is the patient route's rule: assigned to the
caller and still on the ward (the ward service's `visible`), and ranked (the same `decide` the
patient route ranks with: a NOT RANKED patient has no detail page, so no baseline either). Every
call writes one `view_baseline` audit row, on both paths.

The baseline window is the first 24 valid bins from admission when it is known, from the first
stored bin when it is not (every patient present before the run), and from the oldest bin still
kept once the server's 7-day prune or the 672-bin cap has dropped earlier ones: a long stay is
baselined on mid-stay data. `baseline_from`, `anchored_at` and `admission_known` say which. `latest`
is the latest 15-minute median bin, not a single reading."""

import math
from collections.abc import Sequence
from datetime import datetime, timedelta

from biosentry.core.audit import AuditLog
from biosentry.domain.arbiter import NotRanked
from biosentry.domain.computed import NotComputed
from biosentry.domain.scoring.tier_b.baseline import DEFAULTS, VITALS, baseline_finding
from biosentry.repositories.postgres import PostgresStore
from biosentry.schemas import VitalHistory
from biosentry.schemas.baseline import (
    BASELINE_LABEL,
    BaselineComputed,
    BaselineForming,
    BaselineResponse,
    BaselineUnavailable,
    BaselineUsed,
    BaselineVital,
)

from .scoring import decide
from .ward import WardService

BIN = timedelta(minutes=15)
MAX_BINS = 7 * 24 * 4  # 672: 7 days of 15-minute bins
ADMISSION_SLACK = timedelta(minutes=30)  # a window starting this close to admission is "at admission"
ORDER: tuple[BaselineVital, ...] = ("heart_rate", "respiratory_rate", "spo2", "temperature")
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


class BaselineService:
    def __init__(self, store: PostgresStore, audit: AuditLog, wards: WardService) -> None:
        self._store = store
        self._audit = audit
        self._wards = wards

    def baseline(self, clinician_id: str, patient_id: str) -> BaselineResponse | None:
        state = (
            self._store.read(patient_id)
            if patient_id in self._wards.visible(clinician_id)
            else None
        )
        if state is None or isinstance(decide(state), NotRanked):
            self._audit.append(clinician_id, "view_baseline", patient_id, "not_found")
            return None
        self._audit.append(clinician_id, "view_baseline", patient_id, "ok")
        (stay,) = self._store.stays(frozenset({patient_id}))
        now = self._store.now()  # the clock `last_update_s` is measured on
        admitted_at = stay["admitted_at"]
        vitals: list[BaselineComputed | BaselineForming | BaselineUnavailable] = []
        for vital in ORDER:
            # Temperature has no full history in the patient read, so it has no baseline here.
            points = getattr(state.vital_history, vital, None)
            if points is None:
                vitals.append(
                    BaselineUnavailable(vital=vital, status="unavailable", reason="no_history")
                )
                continue
            bins, start = grid(points, admitted_at, vital)
            found = baseline_finding(vital, bins)
            if isinstance(found, NotComputed):
                valid = sum(x is not None for x in bins)
                vitals.append(
                    BaselineForming(
                        vital=vital,
                        status="forming",
                        bins_valid=valid,
                        bins_needed=DEFAULTS.window_bins,
                    )
                    if valid < DEFAULTS.window_bins
                    else BaselineUnavailable(
                        vital=vital, status="unavailable", reason="latest_bin_missing"
                    )
                )
                continue
            finding, dev = found
            assert start is not None  # a computed baseline has at least one bin
            window_start = next(i for i, x in enumerate(bins) if x is not None)
            baseline_from = start + window_start * BIN
            latest_at = start + (len(bins) - 1) * BIN
            vitals.append(
                BaselineComputed(
                    vital=vital,
                    status="computed",
                    baseline_median=round(dev.baseline_median, 1),
                    latest=dev.value,
                    latest_at=latest_at,
                    latest_age_s=max(0, int((now - (latest_at + BIN)).total_seconds())),
                    baseline_from=baseline_from,
                    anchored_at=(
                        "admission"
                        if admitted_at is not None and baseline_from - admitted_at <= ADMISSION_SLACK
                        else "first_kept_bin"
                    ),
                    delta=round(dev.delta, 1),
                    z=round(dev.z, 1),
                    run_bins=dev.run_bins,
                    since=None if dev.since_bin is None else start + dev.since_bin * BIN,
                    significant=finding.significant,
                    direction=finding.direction,
                    pathway=finding.pathway,
                    unit=VITALS[vital].unit,
                )
            )
        return BaselineResponse(
            patient_id=patient_id,
            synthetic=state.synthetic,
            admission_known=admitted_at is not None,
            label=BASELINE_LABEL,
            params=BaselineUsed(window_bins=DEFAULTS.window_bins, k=DEFAULTS.k, p=DEFAULTS.p),
            vitals=vitals,
        )
