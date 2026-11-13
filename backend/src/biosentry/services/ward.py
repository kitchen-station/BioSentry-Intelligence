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

from biosentry.core.audit import AuditLog
from biosentry.domain.arbiter import NotRanked, PolicySignal, Ranked, TierCSignal, rank_ward
from biosentry.domain.computed import Computed, NotComputed
from biosentry.domain.scoring.tier_a import News2Score
from biosentry.domain.scoring.tier_c.confidence import Check
from biosentry.repositories.postgres import OUT_OF_DATE, PostgresStore
from biosentry.schemas import (
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
)
from biosentry.schemas.ingest import LiveWard

from .model_signals import POLICY_ID, STUDENT_ID, ModelSignals, ward_model_signals
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


def _flag_state(value: object) -> str:
    if isinstance(value, Check):
        return {Check.ALERT: "flag", Check.NOT_SURE: "not_sure", Check.QUIET: "no_flag"}[value]
    return "flag" if value else "no_flag"


def _signal(
    result: Computed[Check] | Computed[bool] | NotComputed,
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
    """The two states for a row: prediction needs the student and the check, the policy needs itself."""
    return RowModels(
        prediction=_signal(sig.check, sig, STUDENT_ID, (sig.probability,)),
        alert_policy=_signal(sig.policy, sig, POLICY_ID),
    )


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


def abstained_row(state: UnscoredPatient, decision: NotRanked) -> AbstainedRow:
    return AbstainedRow(
        id=state.id,
        name=state.name,
        age=state.age,
        sex=state.sex,
        bed=state.bed,
        admitting_context=state.admitting_context,
        reason=_reason(state, decision.reason),
        instruction=decision.instruction,
        synthetic=state.synthetic,
    )


class WardService:
    def __init__(self, store: PostgresStore, audit: AuditLog, models: LoadedModels) -> None:
        self._store = store
        self._audit = audit
        self._models = models  # loaded once at start

    def visible(self, clinician_id: str) -> frozenset[str]:
        # An admin reads every patient on the ward; every write is refused earlier by `Writer`.
        if self._store.is_admin(clinician_id):
            return self._store.on_ward()
        return self._store.assigned(clinician_id)

    def _view(
        self, ids: frozenset[str]
    ) -> tuple[list[ScoreRow], list[AbstainedRow], dict[str, UnscoredPatient]]:
        """Rows ranked relative to this set, and the state each row was built from."""
        read = (self._store.read(pid) for pid in sorted(ids))
        states = {s.id: s for s in read if s is not None}
        try:
            signals = ward_model_signals(self._store, states, self._models)  # two statements for the set
        except Exception as e:  # research code must never take the NEWS2 list down
            _log_once(f"{MODEL_ERROR} ({type(e).__name__})")
            failed = NotComputed(MODEL_ERROR)
            signals = {pid: ModelSignals(failed, failed, failed, {}) for pid in states}
        decisions = [
            decide(s, TierCSignal(g.probability, g.check), PolicySignal(g.policy))
            for s, g in ((s, signals[s.id]) for s in states.values())
        ]
        ranking = rank_ward(decisions)
        tiers = self._store.tier_status
        ranked = [
            score_row(states[d.patient_id], rank, d, tiers, row_models(signals[d.patient_id]))
            for rank, d in ranking.ranked
        ]
        abstained = [abstained_row(states[d.patient_id], d) for d in ranking.not_ranked]
        return ranked, abstained, states

    def compute(self, ids: frozenset[str]) -> tuple[list[ScoreRow], list[AbstainedRow]]:
        """Ranked and NOT RANKED rows for these patients, ranked relative to this set."""
        ranked, abstained, _ = self._view(ids)
        return ranked, abstained

    # --- reads ---------------------------------------------------------------------------

    def ward(self, clinician_id: str) -> WardResponse:
        ranked, abstained = self.compute(self.visible(clinician_id))
        self._audit.append_view(clinician_id, "view_ward", self._store.ward)
        return WardResponse(
            ward=self._store.ward,
            clinician=clinician_id,
            shift=SHIFT,
            receiving_data=self._store.receiving_data(),
            sort_tier=Tier.A,
            tier_status=self._store.tier_status,
            ranked=ranked,
            abstained=abstained,
        )

    def detail_or_abstained(
        self, clinician_id: str, patient_id: str
    ) -> PatientDetail | AbstainedRow | None:
        """The patient's detail, or the NOT RANKED row, or None when the caller may not see them.
        Writes no audit row: the caller audits."""
        visible = self.visible(clinician_id)
        if patient_id not in visible:
            return None
        ranked, abstained, states = self._view(visible)
        row = next((r for r in ranked if r.id == patient_id), None)
        if row is None:
            return next((a for a in abstained if a.id == patient_id), None)
        state = states[patient_id]
        breakdown = TierBreakdown(
            tier_a=TierAScore(score=row.score, label=f"NEWS2 = {row.score}"),
        )
        return PatientDetail(
            **row.model_dump(), vital_history=state.vital_history, tier_breakdown=breakdown
        )

    def patient(self, clinician_id: str, patient_id: str) -> PatientDetail | None:
        found = self.detail_or_abstained(clinician_id, patient_id)
        if not isinstance(found, PatientDetail):
            # Missing, not-yours and NOT RANKED (no score to explain) look identical.
            self._audit.append(clinician_id, "view_patient", patient_id, "not_found")
            return None
        self._audit.append_view(clinician_id, "view_patient", patient_id)
        return found

    def stays(self, clinician_id: str) -> list[PatientStay]:
        ids = self._store.assigned(clinician_id, including_left=True)
        self._audit.append(clinician_id, "view_stays", self._store.ward, "ok")
        return [PatientStay.model_validate(row) for row in self._store.stays(ids)]

    def live(self, clinician_id: str) -> LiveWard:
        """The sim's live view: `shared` as stored, and only this clinician's patients' entries."""
        visible = self._store.assigned(clinician_id)  # an admin has no live view, whatever the route
        view = self._store.live_view() or {"shared": None, "patients": []}
        self._audit.append_view(clinician_id, "view_live", self._store.ward)
        return LiveWard.model_validate(
            {
                **view,
                "patients": [p for p in view["patients"] if p["id"] in visible],
                "can_control": self._store.can_control(clinician_id),
            }
        )

    def summaries(self, clinician_id: str) -> list[PatientSummary]:
        ranked, abstained = self.compute(self.visible(clinician_id))
        self._audit.append(clinician_id, "list_patients", "-", "ok")
        return [
            *(PatientSummary(id=r.id, name=r.name, bed=r.bed, ranked=True) for r in ranked),
            *(PatientSummary(id=a.id, name=a.name, bed=a.bed, ranked=False) for a in abstained),
        ]
