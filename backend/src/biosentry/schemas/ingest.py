"""Bodies of the ward simulation's routes (POST /sim/runs, POST /sim/runs/{id}/batch, POST
/sim/runs/{id}/view, GET /sim/runs/{id}/actions, GET /sim/runs/{id}/commands), of the ward
viewer's read of that view (GET /wards/{ward}/live) and of its commands (POST
/wards/{ward}/sim/commands).

Not part of the dashboard contract. Lists are capped; a value is a number or text, or the
reason there is none (I1). Times arrive as minutes from the run's origin.
"""

import json
import math
import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, AwareDatetime, Field, JsonValue, model_validator

from .base import Age, Contract, Flag, Num, Percent
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
BIGINT_MAX = 2**63 - 1  # the database columns for these are BIGINT: more is a 422, not a 500
Seed = Annotated[int, Field(ge=-(2**63), le=BIGINT_MAX)]
Count = Annotated[int, Field(strict=True, ge=0, le=BIGINT_MAX)]


def _sane_origin(moment: datetime) -> datetime:
    """Years 2000 to 2100: outside that, minute offsets and time-zone maths overflow."""
    if not datetime(2000, 1, 1, tzinfo=UTC) <= moment < datetime(2101, 1, 1, tzinfo=UTC):
        raise ValueError("origin must fall in the years 2000 to 2100")
    return moment


Origin = Annotated[AwareDatetime, AfterValidator(_sane_origin)]


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


class Admission(NewRunPatient):
    minute: Minute


class Departure(Contract):
    patient_id: Id
    minute: Minute
    outcome: Literal["discharged", "higher_care"]


class NewRun(Contract):
    client_id: Id  # one per sim page load: the same client may replace its own run, others may not
    source: Literal["synthetic"]
    seed: Seed | None
    origin: Origin
    patients: Annotated[list[NewRunPatient], Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [p.id for p in self.patients]
        if len(ids) != len(set(ids)):
            raise ValueError("patient ids must be unique")
        return self


class Counts(Contract):
    received: Count
    rejected: Count
    abstained: Count


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
    admissions: Annotated[list[Admission], Field(max_length=200)] = []
    departures: Annotated[list[Departure], Field(max_length=200)] = []


VIEW_MAX = 65_536  # characters of compact JSON, per object


_UNSTORABLE = re.compile("[\x00\ud800-\udfff]")


def _storable(value: Any) -> bool:
    """JSONB holds no NUL, lone surrogate, NaN or Infinity (a 422 here, never a 500). The base
    model checks top-level text only."""
    if isinstance(value, str):
        return _UNSTORABLE.search(value) is None
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_storable(v) for v in value)
    if isinstance(value, dict):
        return all(_storable(k) and _storable(v) for k, v in value.items())
    return True


def _small_object(view: dict[str, JsonValue]) -> dict[str, JsonValue]:
    if not _storable(view):
        raise ValueError("a view cannot hold a NUL character, a lone surrogate, NaN or Infinity")
    if len(json.dumps(view, separators=(",", ":"), ensure_ascii=False)) > VIEW_MAX:
        raise ValueError(f"a view is at most {VIEW_MAX} characters of JSON")
    return view


# Any JSON object: the server stores and serves it, and never reads inside it.
View = Annotated[dict[str, JsonValue], AfterValidator(_small_object)]


class PatientView(Contract):
    id: Id
    view: View


class LiveView(Contract):
    """What the ward viewer draws. `shared` goes to every clinician of the ward, so it carries no
    patient names; each patient's `view` goes only to that patient's clinicians."""

    shared: View
    patients: Annotated[list[PatientView], Field(max_length=200)]

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [p.id for p in self.patients]
        if len(ids) != len(set(ids)):
            raise ValueError("patient ids must be unique")
        return self


class LiveWard(Contract):
    """The stored LiveView as one clinician may see it. `shared` is null until a view is stored."""

    shared: View | None
    patients: list[PatientView]
    can_control: Flag  # whether this clinician's role may send the sim commands


class Action(Contract):
    """One successful clinician write, as the ward sim learns of it. A field that does not apply
    to the kind is null. A note's text is never here."""

    seq: int
    kind: Literal["acknowledge", "record_vitals", "add_note", "admit_patient"]
    patient_id: str
    clinician_id: str
    score: int | None = None  # acknowledge: the NEWS2 total acknowledged
    heart_rate: int | None = None  # record_vitals
    respiratory_rate: int | None = None
    spo2: int | None = None
    temperature: float | None = None
    systolic: int | None = None  # record_vitals, admit_patient: both or neither
    diastolic: int | None = None
    acvpu: Literal["A", "C", "V", "P", "U"] | None = None  # record_vitals, admit_patient
    on_oxygen: bool | None = None  # record_vitals
    spo2_scale: Literal[1, 2] | None = None  # record_vitals
    name: str | None = None  # admit_patient, as are the rest
    age: int | None = None
    sex: str | None = None
    bed: str | None = None
    context: str | None = None


class Actions(Contract):
    actions: list[Action]  # at most 200, oldest first


class NewCommand(Contract):
    """What a clinician asks of the simulation. A speed goes with `set_speed` and nothing else; a
    patient goes with the four patient kinds and nothing else; `reset` takes neither."""

    kind: Literal[
        "set_speed",
        "reset",
        "inject_deterioration",
        "start_arrhythmia",
        "detach_patch",
        "apply_patch",
    ]
    patient_id: Id | None = None
    speed: Annotated[int, Field(strict=True)] | None = None  # 0, 1, 10 or 60, checked below

    @model_validator(mode="after")
    def _the_fields_of_the_kind(self) -> Self:
        if self.speed not in (None, 0, 1, 10, 60):
            raise ValueError("speed is 0, 1, 10 or 60")
        patient = self.kind not in ("set_speed", "reset")
        if (self.patient_id is not None) != patient or (self.speed is not None) != (
            self.kind == "set_speed"
        ):
            raise ValueError("a command takes the patient or speed its kind names, and no other")
        return self


class Command(Contract):
    """One accepted command, as the ward sim learns of it. A field that does not apply is null."""

    seq: int
    kind: str
    patient_id: str | None
    clinician_id: str
    speed: int | None = None


class Commands(Contract):
    commands: list[Command]  # at most 200, oldest first
