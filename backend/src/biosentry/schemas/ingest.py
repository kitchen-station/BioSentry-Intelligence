"""Bodies of the ward simulation's routes (POST /sim/runs, POST /sim/runs/{id}/batch).

Not part of the dashboard contract. Lists are capped; a value is a number or text, or the
reason there is none (I1). Times arrive as minutes from the run's origin.
"""

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from .base import Age, Contract, Flag, NonNegInt, Num, Percent
from .clinical import LabPanel
from .common import ProvenanceSource, QualityLevel, Trend

Id = Annotated[str, Field(min_length=1, max_length=64)]
Text = Annotated[str, Field(max_length=500)]
Minute = Annotated[int, Field(strict=True, ge=-1_000_000, le=10_000_000)]
Code = Literal[
    "heart_rate",
    "respiratory_rate",
    "spo2",
    "temperature",
    "blood_pressure",
    "consciousness",
    "supplemental_o2",
    "spo2_scale",
    *(name for name in LabPanel.model_fields if name != "freshness_hours"),
]
Vital = Literal["heart_rate", "respiratory_rate", "spo2", "temperature"]
MAX_ROWS = 20_000


class Obs(Contract):
    patient_id: Id
    code: Code
    minute: Minute
    num: Num | None = None
    num2: Num | None = None  # diastolic
    txt: Literal["A", "C", "V", "P", "U"] | None = None  # ACVPU
    absent_reason: Text | None = None
    source: ProvenanceSource
    quality: QualityLevel

    @model_validator(mode="after")
    def _a_value_or_a_reason(self) -> Self:
        given = {f for f in ("num", "num2", "txt") if getattr(self, f) is not None}
        want = {"blood_pressure": {"num", "num2"}, "consciousness": {"txt"}}.get(self.code, {"num"})
        if given != (want if self.absent_reason is None else set()):
            raise ValueError("give the value this code takes, or the absent_reason, not both")
        return self


class Bin(Contract):
    patient_id: Id
    vital: Vital
    minute: Minute
    value: Num | None  # null is a gap


class Event(Contract):
    minute: Minute
    kind: Annotated[str, Field(max_length=40)]
    patient_id: Id | None
    staff_id: Id | None
    text: Text


class NewRunPatient(Contract):
    id: Id
    name: Annotated[str, Field(max_length=120)]
    age: Age | None
    sex: Annotated[str, Field(max_length=20)] | None
    bed: Annotated[str, Field(max_length=20)]
    context: Text
    story: Text
    rn: Id
    md: Id | None
    trend: Trend
    signal_quality: Percent | None
    observations: Annotated[list[Obs], Field(max_length=MAX_ROWS)]
    bins: Annotated[list[Bin], Field(max_length=MAX_ROWS)]

    @model_validator(mode="after")
    def _own_rows(self) -> Self:
        if any(r.patient_id != self.id for r in (*self.observations, *self.bins)):
            raise ValueError("a patient's observations and bins carry that patient's id")
        return self


class NewRun(Contract):
    source: Literal["synthetic", "eicu_demo"]
    seed: int | None
    origin: AwareDatetime
    patients: Annotated[list[NewRunPatient], Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [p.id for p in self.patients]
        if len(ids) != len(set(ids)):
            raise ValueError("patient ids must be unique")
        return self


class Counts(Contract):
    received: NonNegInt
    rejected: NonNegInt
    abstained: NonNegInt


class BatchPatient(Contract):
    id: Id
    trend: Trend
    signal_quality: Percent | None
    last_sample_minute: Minute | None


class Batch(Contract):
    minute: Minute
    ended: Flag
    counts: Counts
    patients: Annotated[list[BatchPatient], Field(max_length=200)]
    observations: Annotated[list[Obs], Field(max_length=MAX_ROWS)]
    bins: Annotated[list[Bin], Field(max_length=MAX_ROWS)]
    events: Annotated[list[Event], Field(max_length=2_000)]
