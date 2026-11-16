"""Ward rows, abstained rows and the API response envelopes."""

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from .base import Age, Contract, Flag, NonNegInt, NonNegNum, Percent, ScoreNote
from .clinical import LabPanel, VitalHistoryFull, Vitals, VitalTrends
from .common import Provenance, Tier, Trend
from .tiers import EscalationReason, TierStatus


UnitKind = Literal["ranked", "shown", "unranked"]
Risk = Literal["low", "low_medium", "medium", "high"]
ModelState = Literal["flag", "no_flag", "not_sure", "not_computed"]


class ModelSignal(Contract):
    """One research model's result at the last stored 15-minute bin. Synthetic, not validated.
    A flag adds a reason line and changes nothing else. No probability is served."""

    state: ModelState
    reason: str | None  # exactly when not_computed
    bin_start: AwareDatetime | None  # both exactly when computed
    bin_end: AwareDatetime | None
    provenance: list[Provenance]  # empty when not computed


class RowModels(Contract):
    prediction: ModelSignal


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
    escalation_reasons: list[EscalationReason]
    synthetic: Flag
    signal_quality: Percent | None  # null when none is stored (served as stored, never invented)
    last_update_s: NonNegNum | None
    models: RowModels
    # Optional so a client built before units still validates; every answer sets all three.
    unit: str | None = None
    floor: int | None = None  # the unit's floor, null when not known
    person: str | None = None  # the same for every stay of one person, null when the stay stands alone


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
    unit: str | None = None
    floor: int | None = None
    person: str | None = None
    news2_risk: Risk | None = None  # a unit that is shown gives the NEWS2 band, never a score or a rank
    # The last stored value of each vital, whatever its age, each with its own source and time (I2).
    # Shown so a patient whose signal stopped does not look empty. It is never read by the score,
    # the arbiter or the rank: the patient stays NOT RANKED (I1, I4). Optional on the wire only so
    # an older answer still parses; the server always sends it.
    last_seen: Vitals | None = None
    # A patient of a unit that is shown or unranked (ER, ICU): the same vitals and trends a ranked
    # row has, for a signed-in reader who may see the patient. Still no score, no rank (I4). Null on
    # every other NOT RANKED row.
    vitals: Vitals | None = None
    vital_trends: VitalTrends | None = None
    # The kind of the row's unit. The rule above is held here, not only where the row is made: a row
    # that carries `vitals` or `vital_trends` must say its unit is shown or unranked, or it cannot be built.
    unit_kind: UnitKind | None = None

    @model_validator(mode="after")
    def _vitals_only_of_a_shown_or_unranked_unit(self) -> Self:
        if (self.vitals is not None or self.vital_trends is not None) and self.unit_kind not in ("shown", "unranked"):
            raise ValueError("vitals and vital_trends belong to a row of a shown or unranked unit")
        return self


class WardUnit(Contract):
    """One unit of the run (GET /wards): `patients` is how many the caller may see in it now."""

    id: str
    name: str
    floor: int | None
    kind: UnitKind
    patients: NonNegInt
    score_note: ScoreNote | None = None  # how to read this unit's NEWS2, when the run says


class AbstainedDetail(AbstainedRow):
    """GET /patients/{id} for a patient who is NOT RANKED: the row and the stored history. No
    score, rank or breakdown exists to explain."""

    vital_history: VitalHistoryFull
    now: AwareDatetime  # ward time this answer was computed at: ages are measured against it


class WardResponse(Contract):
    ward: str
    name: str | None = None  # the unit's name, floor and kind: optional as the row fields above
    floor: int | None = None
    kind: UnitKind | None = None
    score_note: ScoreNote | None = None  # the unit's note on how to read its NEWS2
    clinician: str
    shift: str
    receiving_data: Flag
    sort_tier: Tier
    tier_status: list[TierStatus]
    ranked: list[ScoreRow]
    abstained: list[AbstainedRow]
    now: AwareDatetime  # ward time this answer was computed at: every age and due time on the page is measured against it


class TierAScore(Contract):
    score: Annotated[int, Field(strict=True, ge=0, le=20)]
    label: str


class TierBreakdown(Contract):
    tier_a: TierAScore | None


class PatientDetail(ScoreRow):
    vital_history: VitalHistoryFull
    tier_breakdown: TierBreakdown
    now: AwareDatetime  # ward time this answer was computed at: every age and due time on the page is measured against it


StayOutcome = Literal["discharged", "higher_care", "transferred", "died"]


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
    unit: str | None = None
    floor: int | None = None
    person: str | None = None
    to_unit: str | None = None  # the unit a transferred patient went to


class PatientSummary(Contract):
    """One line of the patient picker (GET /patients): scoped to the caller."""

    id: str
    name: str
    bed: str
    ranked: Flag
    unit: str
    floor: int | None


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
    signal_quality: Percent | None
    last_update_s: NonNegNum | None
    synthetic: Flag
    admitted_at: AwareDatetime | None  # None: the arrival is not known (I1)
    unit: str
    floor: int | None
    person: str | None
    last_seen: Vitals | None = None  # the newest stored value of each vital: shown on a NOT RANKED row only
