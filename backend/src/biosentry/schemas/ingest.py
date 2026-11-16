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

from pydantic import AfterValidator, AwareDatetime, Field, JsonValue, field_validator, model_validator

from .admission import UNIT_ID
from .base import Age, Contract, Flag, Num, Percent, ScoreNote
from .clinical import LabPanel
from .common import ProvenanceSource, QualityLevel, Trend

Id = Annotated[str, Field(min_length=1, max_length=64)]
UnitId = Annotated[str, Field(pattern=UNIT_ID)]
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
    unit: UnitId | None = None  # null: the run's home unit
    person: Id | None = None  # the same for every stay of one person
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
    outcome: Literal["discharged", "higher_care", "transferred", "died"]
    to_unit: UnitId | None = None

    @model_validator(mode="after")
    def _a_transfer_names_its_unit(self) -> Self:
        if (self.to_unit is not None) != (self.outcome == "transferred"):
            raise ValueError("to_unit goes with the outcome transferred and no other")
        return self



class NewUnit(Contract):
    id: UnitId
    name: Annotated[str, Field(min_length=1, max_length=60)]
    floor: Annotated[int, Field(strict=True, ge=-5, le=200)] | None
    kind: Literal["ranked", "shown", "unranked"]
    score_note: ScoreNote | None = None  # how to read this unit's NEWS2, served with the unit


HOME = NewUnit(id="W4", name="Ward W4", floor=None, kind="ranked")


class NewRun(Contract):
    client_id: Id  # one per sim page load: the same client may replace its own run, others may not
    source: Literal["synthetic"]
    seed: Seed | None
    origin: Origin
    units: Annotated[list[NewUnit], Field(min_length=1, max_length=12)] | None = None  # null: one unit, W4
    patients: Annotated[list[NewRunPatient], Field(min_length=1, max_length=400)]

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [p.id for p in self.patients]
        if len(ids) != len(set(ids)):
            raise ValueError("patient ids must be unique")
        names = [u.id for u in self.units or [HOME]]
        if len(names) != len(set(names)):
            raise ValueError("unit ids must be unique")
        if any(p.unit not in (None, *names) for p in self.patients):
            raise ValueError("a patient's unit is a unit of the run")
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
    patients: Annotated[list[BatchPatient], Field(max_length=400)]
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
    """What the ward viewer draws. `shared` goes to every clinician of the unit, so it carries no
    patient names; each patient's `view` goes only to that patient's clinicians."""

    unit: UnitId | None = None  # null: the run's home unit
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
    now: AwareDatetime  # ward time this answer was computed at: every age and due time on the page is measured against it


BED_LABEL = r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,19}$"
Bed = Annotated[str, Field(pattern=BED_LABEL)]
OPEN_LIST_MAX = 20  # staff and moves
OPEN_BEDS_MAX = 100


class OpenStaff(Contract):
    """A figure on the ward, with no name, id, task or path. `key` is its place in the runner's list."""

    key: Annotated[int, Field(strict=True, ge=0, lt=OPEN_LIST_MAX)]
    role: Literal["rn", "charge_rn", "md"]
    x: Annotated[float, Field(strict=True, allow_inf_nan=False, ge=0, le=100)]
    y: Annotated[float, Field(strict=True, allow_inf_nan=False, ge=0, le=100)]
    facing: Literal["front", "back"]
    walking: Flag
    moved: Annotated[float, Field(strict=True, allow_inf_nan=False, ge=0, le=1e9)]
    stage: Literal["home", "message", "walk", "work", "wait_md", "done", "walk_home"]
    timer: Annotated[float, Field(strict=True, allow_inf_nan=False, ge=-1e6, le=1e6)]


class OpenMove(Contract):
    seq: Annotated[int, Field(strict=True, ge=1, le=2**53 - 1)]
    bed: Bed
    kind: Literal["in", "out"]


class OpenSim(Contract):
    clockS: Annotated[float, Field(strict=True, allow_inf_nan=False, ge=0)]
    speed: Literal[0, 1, 10, 60]
    ended: Flag
    staff: Annotated[list[OpenStaff], Field(max_length=OPEN_LIST_MAX)]
    moves: Annotated[list[OpenMove], Field(max_length=OPEN_LIST_MAX)]

    @field_validator("speed", mode="before")
    @classmethod
    def _speed_is_a_number(cls, value: Any) -> Any:
        if isinstance(value, bool):  # True would pass as 1
            raise ValueError("speed is a number")
        return value


class OpenBed(Contract):
    """One occupied bed: its place in the ward's order and its NEWS2 band, when ranked."""

    bed: Bed
    state: Literal["ranked", "not_ranked"]
    rank: Annotated[int, Field(strict=True, ge=1)] | None
    risk: Literal["low", "low_medium", "medium", "high"] | None


class OpenWard(Contract):
    """What anyone may watch with no sign-in (GET /wards/{ward}/open-view)."""

    ward: str
    synthetic: Literal[True]
    can_control: Literal[False]
    sim: OpenSim | None
    beds: Annotated[list[OpenBed], Field(max_length=OPEN_BEDS_MAX)]


class OpenVital(Contract):
    """One stored vital on a bed card: the value, when it was observed and where it came from."""

    value: Num
    observed_at: AwareDatetime
    source: ProvenanceSource


class OpenPressure(Contract):
    systolic: Annotated[int, Field(strict=True, gt=0)]
    diastolic: Annotated[int, Field(strict=True, gt=0)]
    observed_at: AwareDatetime
    source: ProvenanceSource


class OpenVitals(Contract):
    """The newest stored value of each of the five. All five are NEWS2 inputs, and only a ranked
    bed has a card with vitals, so each one is stored."""

    heart_rate: OpenVital
    respiratory_rate: OpenVital
    spo2: OpenVital
    temperature: OpenVital
    blood_pressure: OpenPressure


class OpenBedCard(Contract):
    """One occupied bed for anyone, with no sign-in (GET /wards/{ward}/open-view/beds/{bed}).
    It holds no name, age, sex, context, note, lab, baseline, model flag, clinician or patient id.
    A bed that is not ranked gives its state and the reason only."""

    unit: str
    floor: int | None  # None: not known
    kind: Literal["ranked", "shown", "unranked"]
    bed: Bed
    synthetic: Literal[True]
    state: Literal["ranked", "not_ranked"]
    score: Annotated[int, Field(strict=True, ge=0, le=20)] | None  # NEWS2, a ranked bed only
    risk: Literal["low", "low_medium", "medium", "high"] | None  # its band, a ranked bed only
    reason: str | None  # why it is not ranked, a bed that is not ranked only
    vitals: OpenVitals | None  # a ranked bed only
    trend: Trend | None  # a ranked bed only


class OpenUnit(Contract):
    """One unit of the run, as anyone may read it: no count, no clinician, no person."""

    id: str
    name: str
    floor: int | None  # None: not known
    kind: Literal["ranked", "shown", "unranked"]
    score_note: ScoreNote | None = None  # how to read this unit's NEWS2, when the run says


class OpenUnits(Contract):
    """The run's units in order, with no sign-in (GET /open-view): what the public page lists."""

    synthetic: Literal[True]
    units: Annotated[list[OpenUnit], Field(max_length=12)]


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
    unit: str | None = None  # admit_patient: where to place the patient


class Actions(Contract):
    actions: list[Action]  # at most 200, oldest first


SKIP_MINUTES = (60, 360, 720, 1440)  # +1 h, +6 h, +12 h, +1 day


class NewCommand(Contract):
    """What a clinician asks of the simulation. A speed goes with `set_speed` and nothing else;
    minutes go with `skip` and nothing else (60, 360, 720 or 1440: the whole run moves ahead); a
    patient goes with the four patient kinds and nothing else; `reset` takes none of them."""

    kind: Literal[
        "set_speed",
        "reset",
        "inject_deterioration",
        "start_arrhythmia",
        "detach_patch",
        "apply_patch",
        "skip",
    ]
    patient_id: Id | None = None
    speed: Annotated[int, Field(strict=True)] | None = None  # 0, 1, 10 or 60, checked below
    minutes: Annotated[int, Field(strict=True)] | None = None  # skip only: 60, 360, 720 or 1440

    @model_validator(mode="after")
    def _the_fields_of_the_kind(self) -> Self:
        if self.speed not in (None, 0, 1, 10, 60):
            raise ValueError("speed is 0, 1, 10 or 60")
        if self.minutes not in (None, *SKIP_MINUTES):
            raise ValueError("minutes is 60, 360, 720 or 1440")
        patient = self.kind not in ("set_speed", "reset", "skip")
        if (
            (self.patient_id is not None) != patient
            or (self.speed is not None) != (self.kind == "set_speed")
            or (self.minutes is not None) != (self.kind == "skip")
        ):
            raise ValueError("a command takes the patient, speed or minutes its kind names, and no other")
        return self


class Command(Contract):
    """One accepted command, as the ward sim learns of it. A field that does not apply is null."""

    seq: int
    kind: str
    patient_id: str | None
    clinician_id: str
    speed: int | None = None
    minutes: int | None = None  # skip only
    unit: str | None = None  # the {ward} the clinician sent it from


class Commands(Contract):
    commands: list[Command]  # at most 200, oldest first
