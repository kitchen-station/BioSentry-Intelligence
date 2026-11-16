"""Read model: the ward, one patient, and the patient picker, scoped to the caller.

Scope is resolved first and fails closed: an unknown clinician sees nobody. A clinician sees the
assigned patients; an admin sees every patient on the ward, read-only. Decisions are the
real news2() and arbiter, computed here on every request. The rank is relative to the caller's
set and is never stored. A patient is ranked only while every NEWS2 input is present; otherwise
the row is NOT RANKED with the arbiter's reason and instruction. The two research models are computed
for the whole set at once and handed to the arbiter as results: a flag adds a reason line and changes
nothing else (synthetic, not validated). Every read appends an audit
record, including reads that find nothing.
"""

import logging
import re
from typing import Any

from pydantic import ValidationError

from biosentry.core.audit import AuditLog
from biosentry.domain.arbiter import Ranked, TierCSignal, rank_ward
from biosentry.domain.computed import Computed, NotComputed
from biosentry.domain.scoring.tier_a import News2Abstained, News2Score, inputs_from_vitals, score_news2
from biosentry.domain.scoring.tier_c.confidence import Check
from biosentry.repositories.postgres import OUT_OF_DATE, PostgresStore, whole
from biosentry.schemas import (
    AbstainedDetail,
    AbstainedRow,
    PatientDetail,
    PatientStay,
    ModelSignal,
    PatientSummary,
    RowModels,
    ScoreRow,
    Tier,
    TierAScore,
    TierBreakdown,
    TierStatus,
    UnscoredPatient,
    WardResponse,
    WardUnit,
)
from biosentry.schemas.ingest import (
    BED_LABEL,
    HISTORY_HOURS,
    HISTORY_POINTS,
    OPEN_BEDS_MAX,
    LiveWard,
    OpenBed,
    OpenBedCard,
    OpenHistory,
    OpenSim,
    OpenWard,
)

from .model_signals import STUDENT_ID, ModelSignals, ward_model_signals
from .models import LoadedModels
from .scoring import decide

log = logging.getLogger(__name__)
_LOGGED: set[str] = set()  # each fixed not-computed reason is logged once per process; no patient, no value

SHIFT = "07:00"
_SHORT = {
    "heart_rate": "HR",
    "respiratory_rate": "RR",
    "spo2": "SpO2",
    "temperature": "Temp",
    "systolic_bp": "SBP",
    "consciousness": "ACVPU",
    "supplemental_o2": "O2",
}
_UNIT = {"heart_rate": "bpm", "respiratory_rate": "/min", "spo2": "%", "temperature": "°C"}


def supporting(news2: News2Score) -> str:
    """The NEWS2 parameters that add points, e.g. "HR 118 bpm, SBP 88"."""
    return ", ".join(
        f"{_SHORT[p.parameter]} {p.value}"
        + (f" {_UNIT[p.parameter]}" if p.parameter in _UNIT else "")
        for p in news2.parameters
        if p.score > 0
    )


MODEL_ERROR = "model error"


def _log_once(reason: str) -> None:
    if reason not in _LOGGED:
        _LOGGED.add(reason)
        log.warning("model result not computed: %s", reason)


def _flag_state(value: Check) -> str:
    return {Check.ALERT: "flag", Check.NOT_SURE: "not_sure", Check.QUIET: "no_flag"}[value]


def _signal(
    result: Computed[Check] | NotComputed,
    sig: ModelSignals,
    model_id: str,
    before: tuple[Computed[float] | NotComputed, ...] = (),
) -> ModelSignal:
    """Served as the models computed it. A not-computed result keeps its fixed reason; no probability."""
    if isinstance(result, NotComputed):
        _log_once(result.reason)
        return ModelSignal(state="not_computed", reason=result.reason, bin_start=None, bin_end=None, provenance=[])
    when = sig.provenance[model_id]
    return ModelSignal(
        state=_flag_state(result.value), reason=None, bin_start=when.bin_start, bin_end=when.bin_end,
        provenance=[c.provenance for c in (*before, result) if isinstance(c, Computed)],
    )  # fmt: skip


def row_models(sig: ModelSignals) -> RowModels:
    """The state for a row: the prediction needs the student and the check."""
    return RowModels(prediction=_signal(sig.check, sig, STUDENT_ID, (sig.probability,)))


def score_row(
    state: UnscoredPatient, rank: int, decision: Ranked, tiers: list[TierStatus], models: RowModels
) -> ScoreRow:
    return ScoreRow(
        id=state.id,
        rank=rank,
        score=decision.news2.total,
        trend=state.trend,
        name=state.name,
        age=state.age,
        sex=state.sex,
        bed=state.bed,
        admitting_context=state.admitting_context,
        mechanism=state.mechanism,
        supporting=state.supporting or supporting(decision.news2),
        vitals=state.vitals,
        vital_trends=state.vital_trends,
        labs=state.labs,
        sort_tier=Tier.A,
        tier_status=tiers,
        escalation_reasons=decision.escalation_reasons(),
        models=models,
        synthetic=state.synthetic,
        signal_quality=state.signal_quality,
        last_update_s=state.last_update_s,
        unit=state.unit,
        floor=state.floor,
        person=state.person,
    )


_LABEL = {
    "heart_rate": "Heart rate",
    "respiratory_rate": "Respiratory rate",
    "spo2": "SpO2",
    "temperature": "Temperature",
}


def _reason(state: UnscoredPatient, reason: str) -> str:
    """Say "out of date" for a nurse value that expired. The arbiter only knows it is missing."""
    old = [
        label
        for code, label in _LABEL.items()
        if getattr(getattr(state.vitals, code).data, "reason", None) == OUT_OF_DATE
    ]
    if reason.startswith("No device data"):
        return f"{reason}; {', '.join(old)} out of date" if old else reason
    for label in old:
        reason = reason.replace(f"{label} missing", f"{label} out of date")
    return reason


def abstained_row(
    state: UnscoredPatient, reason: str, instruction: str, news2_risk: str | None = None
) -> AbstainedRow:
    return AbstainedRow(
        id=state.id,
        name=state.name,
        age=state.age,
        sex=state.sex,
        bed=state.bed,
        admitting_context=state.admitting_context,
        reason=reason,
        instruction=instruction,
        synthetic=state.synthetic,
        unit=state.unit,
        floor=state.floor,
        person=state.person,
        news2_risk=news2_risk,  # type: ignore[arg-type]
        last_seen=state.last_seen,
    )


def unit_row(state: UnscoredPatient, unit: dict[str, Any]) -> AbstainedRow:
    """A patient of a unit that is shown or unranked: never a rank or a score. A shown unit gives
    the NEWS2 band when every input is there; an unranked unit never does."""
    tier_a = score_news2(inputs_from_vitals(state.vitals))
    risk = None if unit["kind"] == "unranked" or isinstance(tier_a, News2Abstained) else tier_a.risk.value
    verb = "shown, not ranked" if unit["kind"] == "shown" else "not ranked"
    row = abstained_row(
        state, f"{unit['name']} is {verb}: NEWS2 is a ward score.", "No NEWS2 rank for this unit.", risk
    )
    # The stored vitals, for a reader who may see the patient (the public views do not use this row).
    return row.model_copy(update={"vitals": state.vitals, "vital_trends": state.vital_trends})


# where a patient went is not shown
_OPEN_KIND = {"in": "in", "discharged": "out", "higher_care": "out", "transferred": "out", "died": "out"}


def open_sim(view: dict[str, Any] | None) -> OpenSim | None:
    """The runner's view as the open answer draws it: each field picked by name, nothing copied
    whole. Any field that fails its check, or too many entries, gives None (never a partial sim)."""
    shared = view.get("shared") if view else None
    try:
        return OpenSim.model_validate(
            {
                **{k: shared[k] for k in ("clockS", "speed", "ended")},
                "staff": [
                    {
                        "key": i,
                        **{k: s[k] for k in ("role", "x", "y", "facing", "moved", "stage", "timer")},
                        "walking": len(s["path"]) > 0 if isinstance(s["path"], list) else None,
                    }
                    for i, s in enumerate(shared["staff"])
                ],
                "moves": [
                    {"seq": m["seq"], "bed": m["bed"], "kind": _OPEN_KIND.get(m["kind"])}
                    for m in shared["moves"]
                ],
            }
        )
    except (KeyError, TypeError, ValidationError):  # shared is missing, not an object, or not as read
        return None


class WardService:
    def __init__(self, store: PostgresStore, audit: AuditLog, models: LoadedModels) -> None:
        self._store = store
        self._audit = audit
        self._models = models  # loaded once at start

    def visible(self, clinician_id: str, unit: str | None = None) -> frozenset[str]:
        # An admin reads every patient on the ward; every write is refused earlier by `Writer`.
        # A unit only narrows: the assignment decides, never the unit.
        if self._store.is_admin(clinician_id):
            return self._store.on_ward(unit)
        return self._store.assigned(clinician_id, unit=unit)

    def view(
        self, ids: frozenset[str]
    ) -> tuple[list[ScoreRow], list[AbstainedRow], dict[str, UnscoredPatient]]:
        """Rows ranked relative to this set, and the state each row was built from."""
        read = (self._store.read(pid) for pid in sorted(ids))
        every = {s.id: s for s in read if s is not None}
        units = self._store.units
        # Only a ranked unit is scored: no score, rank or model flag is computed for the others.
        states = {pid: s for pid, s in every.items() if units[s.unit]["kind"] == "ranked"}
        try:
            signals = ward_model_signals(self._store, states, self._models)  # two statements for the set
        except Exception as e:  # research code must never take the NEWS2 list down
            _log_once(f"{MODEL_ERROR} ({type(e).__name__})")
            _LOGGED.add(MODEL_ERROR)  # the line above already says it, with the kind of error
            failed = NotComputed(MODEL_ERROR)
            signals = {pid: ModelSignals(failed, failed, {}) for pid in states}
        decisions = [
            decide(s, TierCSignal(g.probability, g.check))
            for s, g in ((s, signals[s.id]) for s in states.values())
        ]
        tiers = self._store.tier_status
        ranked: list[ScoreRow] = []
        abstained: list[AbstainedRow] = []
        for unit in units:  # a rank is within one unit
            ranking = rank_ward(d for d in decisions if states[d.patient_id].unit == unit)
            ranked += [
                score_row(states[d.patient_id], rank, d, tiers, row_models(signals[d.patient_id]))
                for rank, d in ranking.ranked
            ]
            abstained += [
                abstained_row(states[d.patient_id], _reason(states[d.patient_id], d.reason), d.instruction)
                for d in ranking.not_ranked
            ]
        abstained += [unit_row(s, units[s.unit]) for s in every.values() if s.id not in states]
        return ranked, abstained, every

    def compute(self, ids: frozenset[str]) -> tuple[list[ScoreRow], list[AbstainedRow]]:
        """Ranked and NOT RANKED rows for these patients, ranked relative to this set."""
        ranked, abstained, _ = self.view(ids)
        return ranked, abstained

    # --- reads ---------------------------------------------------------------------------

    def units(self, clinician_id: str) -> list[WardUnit]:
        counts = self._store.counts(self.visible(clinician_id))
        self._audit.append(clinician_id, "list_wards", "-", "ok")
        return [WardUnit(**u, patients=counts.get(u["id"], 0)) for u in self._store.units.values()]

    def ward(self, clinician_id: str, unit: str | None = None) -> WardResponse:
        unit = unit or self._store.ward  # None: the home unit
        ranked, abstained = self.compute(self.visible(clinician_id, unit))
        self._audit.append_view(clinician_id, "view_ward", unit)
        info = self._store.units[unit]
        return WardResponse(
            ward=unit,
            name=info["name"],
            floor=info["floor"],
            kind=info["kind"],
            clinician=clinician_id,
            shift=SHIFT,
            receiving_data=self._store.receiving_data(),
            sort_tier=Tier.A,
            tier_status=self._store.tier_status,
            ranked=ranked,
            abstained=abstained,
            now=whole(self._store.now()),
        )

    def detail_or_abstained(
        self, clinician_id: str, patient_id: str
    ) -> PatientDetail | AbstainedDetail | None:
        """The patient's detail, or the NOT RANKED row with the stored history, or None when the
        caller may not see them.
        Writes no audit row: the caller audits."""
        visible = self.visible(clinician_id)
        if patient_id not in visible:
            return None
        ranked, abstained, states = self.view(visible)
        row = next((r for r in ranked if r.id == patient_id), None)
        if row is None:
            held = next((a for a in abstained if a.id == patient_id), None)
            return held and AbstainedDetail(
                **held.model_dump(),
                vital_history=states[patient_id].vital_history,
                now=whole(self._store.now()),
            )
        state = states[patient_id]
        breakdown = TierBreakdown(
            tier_a=TierAScore(score=row.score, label=f"NEWS2 = {row.score}"),
        )
        return PatientDetail(
            **row.model_dump(),
            vital_history=state.vital_history,
            tier_breakdown=breakdown,
            now=whole(self._store.now()),
        )

    def patient(
        self, clinician_id: str, patient_id: str
    ) -> PatientDetail | AbstainedDetail | None:
        """The page's read. A NOT RANKED patient gets the row and the stored history (no score to
        explain); a missing patient and one who is not the caller's look identical (None)."""
        found = self.detail_or_abstained(clinician_id, patient_id)
        if found is None:
            self._audit.append(clinician_id, "view_patient", patient_id, "not_found")
            return None
        self._audit.append_view(clinician_id, "view_patient", patient_id)
        return found

    def stays(self, clinician_id: str, unit: str) -> list[PatientStay]:
        ids = self._store.assigned(clinician_id, including_left=True, unit=unit)
        self._audit.append(clinician_id, "view_stays", unit, "ok")
        return [PatientStay.model_validate(row) for row in self._store.stays(ids, unit)]

    def live(self, clinician_id: str, unit: str | None = None) -> LiveWard:
        """The unit's (None: the home unit's) live view: `shared` as stored, and only this
        clinician's patients' entries."""
        unit = unit or self._store.ward
        visible = self._store.assigned(clinician_id, unit=unit)  # an admin has no live view, whatever the route
        view = self._store.live_view(unit) or {"shared": None, "patients": []}
        self._audit.append_view(clinician_id, "view_live", unit)
        return LiveWard.model_validate(
            {
                **view,
                "patients": [p for p in view["patients"] if p["id"] in visible],
                "can_control": self._store.can_control(clinician_id),
                "now": whole(self._store.now()),
            }
        )

    def open_ward(self, unit: str) -> OpenWard | None:
        """The unit for a caller nobody identified: bed, rank and NEWS2 band only, from the unit's
        own scoring, and the simulator's figures. None when the unit holds too many patients.
        No model runs, so no model state can reach it; the ranking does not depend on one. A unit
        that is not ranked shows every bed as not ranked. Writes no audit row: the route does."""
        ids = self._store.on_ward(unit)
        if len(ids) > OPEN_BEDS_MAX:
            return None
        states = [s for s in (self._store.read(pid) for pid in sorted(ids)) if s is not None]
        bed = {s.id: s.bed for s in states}
        if self._store.units[unit]["kind"] == "ranked":
            ranking = rank_ward(decide(s) for s in states)
            beds = [
                *(("ranked", rank, d.patient_id, d.news2.risk.value) for rank, d in ranking.ranked),
                *(("not_ranked", None, d.patient_id, None) for d in ranking.not_ranked),
            ]
        else:
            beds = [("not_ranked", None, s.id, None) for s in states]
        shown = []
        for state, rank, pid, risk in beds:
            try:
                shown.append(OpenBed(bed=bed[pid], state=state, rank=rank, risk=risk))
            except ValidationError:  # a label that breaks the rule is left out
                continue
        return OpenWard(
            ward=unit,
            synthetic=True,
            can_control=False,
            sim=open_sim(self._store.live_view(unit)),
            beds=sorted(shown, key=lambda b: (b.bed, b.rank or 0)),
        )

    def open_bed(self, unit: str, bed: str) -> OpenBedCard | None:
        """One bed's card for a caller nobody identified. A ranked bed: its NEWS2 score and band,
        the newest stored value of five vitals with time and source, the last 12 hours of the five
        (15-minute values, gaps kept, no id or score on a point), and the trend word. Any other
        bed: its state and the reason only. None: no such bed, or the stay has ended. Nothing
        that names the patient is read into it, and no model runs. Writes no audit row: the
        route does."""
        pid = self._store.in_bed(unit, bed) if re.fullmatch(BED_LABEL, bed) else None
        state = self._store.read(pid) if pid else None
        if state is None:
            return None
        info = self._store.units[unit]
        place = {"unit": unit, "floor": info["floor"], "kind": info["kind"], "bed": bed, "synthetic": True}
        decision = decide(state) if info["kind"] == "ranked" else None
        if not isinstance(decision, Ranked):
            reason = unit_row(state, info).reason if decision is None else _reason(state, decision.reason)
            return OpenBedCard(
                **place, state="not_ranked", score=None, risk=None, reason=reason, vitals=None, trend=None, history=None
            )
        vitals: dict[str, Any] = {}
        # All five are NEWS2 inputs: a ranked patient has each one stored.
        for code in ("heart_rate", "respiratory_rate", "spo2", "temperature", "blood_pressure"):
            m = getattr(state.vitals, code)
            value = m.data.value.model_dump() if code == "blood_pressure" else {"value": m.data.value}
            vitals[code] = {**value, "observed_at": m.provenance.observed_at, "source": m.provenance.source}
        return OpenBedCard(
            **place,
            state="ranked",
            score=decision.news2.total,
            risk=decision.news2.risk.value,
            reason=None,
            vitals=vitals,
            trend=state.trend,
            history=OpenHistory(hours=HISTORY_HOURS, **self._store.open_history(state.id, HISTORY_HOURS, HISTORY_POINTS)),
        )

    def summaries(self, clinician_id: str) -> list[PatientSummary]:
        ranked, abstained = self.compute(self.visible(clinician_id))
        self._audit.append(clinician_id, "list_patients", "-", "ok")
        return [
            *(PatientSummary(id=r.id, name=r.name, bed=r.bed, ranked=True, unit=r.unit, floor=r.floor) for r in ranked),
            *(PatientSummary(id=a.id, name=a.name, bed=a.bed, ranked=False, unit=a.unit, floor=a.floor) for a in abstained),
        ]
