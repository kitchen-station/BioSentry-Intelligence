"""Ward rows, abstained rows and the API response envelopes."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from .base import Age, Contract, Flag, NonNegNum, Percent
from .clinical import LabPanel, VitalHistoryFull, Vitals, VitalTrends
from .common import Tier, Trend
from .tiers import EscalationReason, TierBData, TierCData, TierStatus


class ScoreRow(Contract):
    """A ranked patient: carries a NEWS2 score and rank. That every required input was
    present is enforced by the scorer and arbiter (CP4/5), not by this schema (I1, I4)."""

    id: str
    rank: Annotated[int, Field(strict=True, ge=1)]
    score: Annotated[int, Field(strict=True, ge=0, le=20)]  # NEWS2 aggregate, 0-20
    trend: Trend
    name: str
    age: Age | None
    sex: str | None
    bed: str
    admitting_context: str
    mechanism: str
    supporting: str
    vitals: Vitals
    vital_trends: VitalTrends
    labs: LabPanel
    sort_tier: Tier
    tier_status: list[TierStatus]
    tier_b: TierBData | None
    tier_c: TierCData | None
    escalation_reasons: list[EscalationReason]
    synthetic: Flag
    signal_quality: Percent | None  # null when none is stored (served as stored, never invented)
    last_update_s: NonNegNum | None


class AbstainedRow(Contract):
    """NOT RANKED is a first-class row (I4): a reason and an instruction, no score, no rank."""

    id: str
    name: str
    age: Age | None
    sex: str | None
    bed: str
    admitting_context: str
    reason: str
    instruction: str
    synthetic: Flag


class WardResponse(Contract):
    ward: str
    clinician: str
    shift: str
    receiving_data: Flag
    sort_tier: Tier
    tier_status: list[TierStatus]
    ranked: list[ScoreRow]
    abstained: list[AbstainedRow]


class TierAScore(Contract):
    score: Annotated[int, Field(strict=True, ge=0, le=20)]
    label: str


class TierBreakdown(Contract):
    tier_a: TierAScore | None
    tier_b: TierBData | None
    tier_c: TierCData | None


class PatientDetail(ScoreRow):
    vital_history: VitalHistoryFull
    tier_breakdown: TierBreakdown


StayOutcome = Literal["discharged", "higher_care"]


class PatientStay(Contract):
    """One stay on the ward (GET /wards/{ward}/stays): scoped to the caller. Facts only: a score
    is computed on read and never stored."""

    id: str
    name: str
    bed: str
    admitted_at: AwareDatetime | None  # None: the arrival is not known (I1)
    left_at: AwareDatetime | None  # None: still on the ward
    outcome: StayOutcome | None
    synthetic: Flag


class PatientSummary(Contract):
    """One line of the patient picker (GET /patients): scoped to the caller."""

    id: str
    name: str
    bed: str
    ranked: Flag


class UnscoredPatient(Contract):
    """Everything known about a patient before any score exists.

    This is the state behind both a ranked row and a NOT RANKED row: scores, ranks and
    reasons are computed from it on read (never stored), so a recorded value re-ranks the
    patient. `signal_quality` and `last_update_s` are null for a patient with no patch.
    """

    id: str
    name: str
    age: Age | None
    sex: str | None
    bed: str
    admitting_context: str
    trend: Trend
    mechanism: str
    supporting: str
    vitals: Vitals
    vital_trends: VitalTrends
    vital_history: VitalHistoryFull
    labs: LabPanel
    tier_b: TierBData | None
    tier_c: TierCData | None
    signal_quality: Percent | None
    last_update_s: NonNegNum | None
    synthetic: Flag
    admitted_at: AwareDatetime | None  # None: the arrival is not known (I1)
