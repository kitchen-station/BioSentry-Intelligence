"""Read model: the ward, one patient, and the patient picker, scoped to the caller.

Scope is resolved first and fails closed: an unknown clinician sees nobody. Decisions are the
real news2() and arbiter, computed here on every request. The rank is relative to the caller's
set and is never stored. A patient is ranked only while every NEWS2 input is present; otherwise
the row is NOT RANKED with the arbiter's reason and instruction. Every read appends an audit
record, including reads that find nothing.
"""

from biosentry.core.audit import AuditLog
from biosentry.domain.arbiter import NotRanked, Ranked, rank_ward
from biosentry.domain.scoring.tier_a import News2Score
from biosentry.repositories.postgres import PostgresStore
from biosentry.schemas import (
    AbstainedRow,
    PatientDetail,
    PatientSummary,
    ScoreRow,
    Tier,
    TierAScore,
    TierBreakdown,
    TierStatus,
    UnscoredPatient,
    WardResponse,
)

from .scoring import decide

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


def score_row(
    state: UnscoredPatient, rank: int, decision: Ranked, tiers: list[TierStatus]
) -> ScoreRow:
    if state.signal_quality is None or state.last_update_s is None:
        raise ValueError(f"{state.id} was ranked without a patch signal")
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
        tier_b=state.tier_b,
        tier_c=state.tier_c,
        escalation_reasons=decision.escalation_reasons(),
        synthetic=state.synthetic,
        signal_quality=state.signal_quality,
        last_update_s=state.last_update_s,
    )


def abstained_row(state: UnscoredPatient, decision: NotRanked) -> AbstainedRow:
    return AbstainedRow(
        id=state.id,
        name=state.name,
        age=state.age,
        sex=state.sex,
        bed=state.bed,
        admitting_context=state.admitting_context,
        reason=decision.reason,
        instruction=decision.instruction,
        synthetic=state.synthetic,
    )


class WardService:
    def __init__(self, store: PostgresStore, audit: AuditLog) -> None:
        self._store = store
        self._audit = audit

    def visible(self, clinician_id: str) -> frozenset[str]:
        return self._store.assigned(clinician_id)

    def _view(
        self, ids: frozenset[str]
    ) -> tuple[list[ScoreRow], list[AbstainedRow], dict[str, UnscoredPatient]]:
        """Rows ranked relative to this set, and the state each row was built from."""
        read = (self._store.read(pid) for pid in sorted(ids))
        states = {s.id: s for s in read if s is not None}
        ranking = rank_ward(decide(s) for s in states.values())
        tiers = self._store.tier_status
        ranked = [score_row(states[d.patient_id], rank, d, tiers) for rank, d in ranking.ranked]
        abstained = [abstained_row(states[d.patient_id], d) for d in ranking.not_ranked]
        return ranked, abstained, states

    def compute(self, ids: frozenset[str]) -> tuple[list[ScoreRow], list[AbstainedRow]]:
        """Ranked and NOT RANKED rows for these patients, ranked relative to this set."""
        ranked, abstained, _ = self._view(ids)
        return ranked, abstained

    # --- reads ---------------------------------------------------------------------------

    def ward(self, clinician_id: str) -> WardResponse:
        ranked, abstained = self.compute(self.visible(clinician_id))
        self._audit.append(clinician_id, "view_ward", self._store.ward, "ok")
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

    def patient(self, clinician_id: str, patient_id: str) -> PatientDetail | None:
        visible = self.visible(clinician_id)
        if patient_id not in visible:
            # Missing and not-yours look identical to the caller (and in the log).
            self._audit.append(clinician_id, "view_patient", patient_id, "not_found")
            return None
        ranked, _, states = self._view(visible)
        row = next((r for r in ranked if r.id == patient_id), None)
        if row is None:  # NOT RANKED: no detail page (there is no score to explain)
            self._audit.append(clinician_id, "view_patient", patient_id, "not_found")
            return None
        self._audit.append(clinician_id, "view_patient", patient_id, "ok")
        state = states[patient_id]
        breakdown = TierBreakdown(
            tier_a=TierAScore(score=row.score, label=f"NEWS2 = {row.score}"),
            tier_b=state.tier_b,
            tier_c=state.tier_c,
        )
        return PatientDetail(
            **row.model_dump(), vital_history=state.vital_history, tier_breakdown=breakdown
        )

    def summaries(self, clinician_id: str) -> list[PatientSummary]:
        ranked, abstained = self.compute(self.visible(clinician_id))
        self._audit.append(clinician_id, "list_patients", "-", "ok")
        return [
            *(PatientSummary(id=r.id, name=r.name, bed=r.bed, ranked=True) for r in ranked),
            *(PatientSummary(id=a.id, name=a.name, bed=a.bed, ranked=False) for a in abstained),
        ]
