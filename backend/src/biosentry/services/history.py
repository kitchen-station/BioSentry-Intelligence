"""A patient's stay as a short history: when the NEWS2 was in an alert state, how high, why, and what
was done. Read-only and display only: nothing that ranks or scores a patient imports this module.

No score is stored. Every stored 15-minute bin is rebuilt on read (`as_of.news2_as_of`, then the
arbiter's own decision) and the bins are folded into runs of one state. Who may read is the patient
page's rule: visible to the caller now and ranked. Every call writes one `view_history` audit row.
Research code: synthetic data only, not validated.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby
from typing import Any, Literal

from biosentry.core.audit import AuditLog
from biosentry.domain.arbiter import NotRanked, arbitrate
from biosentry.domain.computed import NotComputed
from biosentry.domain.scoring.tier_a import Risk
from biosentry.repositories.postgres import NURSE_VALID, PostgresStore, StoredObservation
from biosentry.schemas.history import (
    HISTORY_LABEL,
    HistoryEvent,
    HistoryProvenance,
    HistoryRun,
    PatientHistory,
)

from .as_of import news2_as_of
from .model_signals import bin_window, prepare_series
from .scoring import BIN, decide
from .ward import WardService

MAX_RUNS = 200
MAX_EVENTS = 300
NOT_REBUILT = "A value the bin needs is not stored"
NO_READING = "No usable heart rate reading"
LEVEL = {Risk.low_medium: "single_3", Risk.medium: "medium", Risk.high: "high"}
ROLES = {"rn", "md", "charge_rn"}
State = Literal["alert", "quiet", "not_ranked", "not_rebuilt", "no_reading"]


@dataclass(frozen=True, slots=True)
class BinState:
    state: State
    total: int | None = None  # alert only
    level: str | None = None  # alert only
    reasons: tuple[str, ...] = ()


QUIET = BinState("quiet")


def classify(device: dict[str, float | None], rows: Sequence[StoredObservation], end: datetime, *, earlier: bool,
             patient_id: str = "") -> BinState:
    """The state of the bin ending at `end` from its four stored values and the stored rows."""
    if device["heart_rate"] is None:
        return BinState("no_reading", reasons=(NO_READING,))
    rebuilt = news2_as_of(device, rows, end, earlier=earlier)
    if rebuilt is None:
        return BinState("not_rebuilt", reasons=(NOT_REBUILT,))
    decision = arbitrate(patient_id, rebuilt.result)  # the arbiter's own rule and texts
    if isinstance(decision, NotRanked):
        return BinState("not_ranked", reasons=(decision.reason,))
    if not decision.escalated:
        return QUIET
    return BinState("alert", decision.news2.total, LEVEL[decision.news2.risk], tuple(r.text for r in decision.reasons))


def fold(bins: Sequence[BinState], start: datetime, computed_at: datetime, synthetic: bool) -> tuple[list[HistoryRun], bool]:
    """Runs of consecutive bins of one state, oldest first, the newest MAX_RUNS kept. `start` is the
    stored bin_at of bin 0. A quiet bin ends a run and is not returned."""
    provenance = HistoryProvenance(source="derived", tier="A", rule="news2_as_of_bins", computed_at=computed_at, synthetic=synthetic)
    runs = []
    for state, group in groupby(enumerate(bins), key=lambda p: p[1].state):
        ks = [k for k, _ in group]
        if state == "quiet":
            continue
        # The peak is the highest total; the earliest bin wins a tie. Other states show their first bin's reason.
        peak = max(ks, key=lambda k: (bins[k].total or 0, -k)) if state == "alert" else ks[0]
        at = bin_window(start + peak * BIN)[0]
        runs.append(
            HistoryRun(
                state=state,
                start=bin_window(start + ks[0] * BIN)[0],
                end=bin_window(start + ks[-1] * BIN)[1],
                ongoing=ks[-1] == len(bins) - 1,
                bins=len(ks),
                peak_score=bins[peak].total,
                peak_level=bins[peak].level,  # type: ignore[arg-type]
                peak_at=at if state == "alert" else None,
                reasons=list(bins[peak].reasons),
                provenance=provenance,
            )
        )
    return runs[-MAX_RUNS:], len(runs) > MAX_RUNS


def events(
    admitted_at: datetime | None,
    rows: Sequence[StoredObservation],
    stored: Sequence[dict[str, Any]],
    start: datetime,
    end: datetime,
) -> tuple[list[HistoryEvent], bool]:
    """What was done, oldest first, the newest MAX_EVENTS kept. `stored` is `history_events`. A nurse's
    blood pressure is charted when a row of that code has source nurse and a value. The admission is the
    start of the stay, so it is kept when it is not after the grid's end."""
    found = [
        HistoryEvent(kind="bp_charted", at=o.observed_at, by=None, role=None, score=None, note_id=None, source="observation")
        for o in rows
        if o.code == "blood_pressure" and o.source == "nurse" and o.absent_reason is None and o.num is not None
        and start <= o.observed_at <= end
    ]
    kinds = {"acknowledge": "acknowledged", "record_vitals": "vitals_recorded", "add_note": "note_added"}
    found += [
        HistoryEvent(
            kind=kinds[s["kind"]],
            at=s["at"],
            by=s["actor"],
            role=s["role"].lower() if s["role"] and s["role"].lower() in ROLES else None,  # typed by the client
            score=s["score"],
            note_id=s["note_id"],
            source=s["source"],
        )
        for s in stored
    ]
    if admitted_at is not None and admitted_at <= end:
        found.append(HistoryEvent(kind="admitted", at=admitted_at, by=None, role=None, score=None, note_id=None, source="patient"))
    found.sort(key=lambda e: (e.at, e.kind))
    return found[-MAX_EVENTS:], len(found) > MAX_EVENTS


class HistoryService:
    def __init__(self, store: PostgresStore, audit: AuditLog, wards: WardService) -> None:
        self._store = store
        self._audit = audit
        self._wards = wards

    def history(self, clinician_id: str, patient_id: str) -> PatientHistory | None:
        found = self.compute(clinician_id, patient_id)
        if found is None:  # missing, not yours, left and NOT RANKED look the same
            self._audit.append(clinician_id, "view_history", patient_id, "not_found")
            return None
        self._audit.append(clinician_id, "view_history", patient_id, "ok")  # not a timed read
        return found

    def compute(self, clinician_id: str, patient_id: str) -> PatientHistory | None:
        """The history, or None when the caller may not see the patient or they are NOT RANKED.
        Writes no audit row: the caller audits."""
        state = self._store.read(patient_id) if patient_id in self._wards.visible(clinician_id) else None
        if state is None or isinstance(decide(state), NotRanked):
            return None
        store = self._store
        h = state.vital_history
        temperature = store.temperature_bins([patient_id]).get(patient_id, [])
        points = {"heart_rate": h.heart_rate, "respiratory_rate": h.respiratory_rate, "spo2": h.spo2, "temperature": temperature}
        prep = prepare_series(points, state.admitted_at)
        whole = dict(patient_id=patient_id, synthetic=state.synthetic, label=HISTORY_LABEL)
        if isinstance(prep, NotComputed):  # no stored bins, or more than the grid can hold
            return PatientHistory(**whole, grid_from=None, grid_to=None, bins=0, runs=[], runs_cut=False, events=[], events_cut=False)
        start, end = bin_window(prep.at(0))[0], bin_window(prep.at(prep.last))[1]
        # From 12 hours before the first bin's end: the rebuild's own window (a nurse-typed device value).
        rows = store.observations_between([(patient_id, bin_window(prep.at(0))[1] - NURSE_VALID, end)]).get(patient_id, [])
        bins = [
            classify(prep.device(k), rows, bin_window(prep.at(k))[1], earlier=k < prep.last, patient_id=patient_id)
            for k in range(prep.last + 1)
        ]
        runs, runs_cut = fold(bins, prep.start, store.now(), state.synthetic)
        stored = store.history_events(patient_id, start, end, MAX_EVENTS + 1)
        done, events_cut = events(state.admitted_at, rows, stored, start, end)
        return PatientHistory(
            **whole, grid_from=start, grid_to=end, bins=prep.last + 1,
            runs=runs, runs_cut=runs_cut, events=done, events_cut=events_cut,
        )
