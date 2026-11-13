"""Bodies of the Simulation lab (contract-d7): a clinician's request, the job the lab worker claims,
the worker's report, and what a clinician reads back. Every value of a compare request is a
fixed-list name or an integer in range. A patient question (contract-patient-ask) is one line of
free text that the API stores and never reads, and the allow-listed data of one patient."""

import re
from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, Discriminator, Field, Tag, model_validator

from .base import Contract, Flag, NonNegNum, Percent
from .baseline import BaselineComputed, BaselineForming, BaselineUnavailable, BaselineUsed
from .clinical import VitalHistoryFull, Vitals
from .common import Trend
from .tiers import EscalationReason

W4_RULES = (*(f"news2_total_ge_{k}" for k in range(1, 21)), "news2_chart_trigger")
_TO_DOH = ("staffing=doh-standard", "nurses=doh-standard", "doctors=doh-standard")
HOSPITAL_PAIRS = {
    "typical": _TO_DOH,
    "surge": _TO_DOH,
    "doh-standard": ("staffing=typical", "nurses=typical", "doctors=typical"),
}

# Written once: C0 (LF apart), DEL, C1, the line and paragraph separators and the direction controls.
_NO_LF = r"\x00-\x09\x0b-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069"
_BAD = re.compile(f"[{_NO_LF}]")
_BAD_OR_LF = re.compile(f"[\\n{_NO_LF}]")


def _clean(bad: re.Pattern[str]) -> Any:
    def check(value: str) -> str:
        if bad.search(value):
            raise ValueError("a control or direction character")
        value.encode("utf-8")  # a lone surrogate is a ValueError here, not a 500 in the database
        return value

    return AfterValidator(check)


OneLine = Annotated[str, Field(min_length=1, max_length=300), _clean(_BAD_OR_LF)]
Question = Annotated[str, Field(min_length=1, max_length=500), _clean(_BAD_OR_LF)]
State = Literal["queued", "running", "done", "failed", "refused"]


class NewLabRun(Contract):
    kind: Literal["compare"]
    mode: Literal["w4", "hospital"]
    seed: Annotated[int, Field(strict=True, ge=0, le=2147483647)]
    days: Annotated[int, Field(strict=True, ge=1, le=30)]
    baseline: Annotated[str, Field(max_length=40)]
    change: Annotated[str, Field(max_length=40)]

    @model_validator(mode="after")
    def _from_the_fixed_lists(self) -> Self:
        if self.mode == "w4":
            ok = self.baseline in W4_RULES and self.change in W4_RULES and self.baseline != self.change
        else:
            ok = self.change in HOSPITAL_PAIRS.get(self.baseline, ())
        if not ok:
            raise ValueError("baseline and change must come from the lab's fixed lists")
        return self


class LabJob(Contract):
    id: int
    kind: Literal["compare"]
    mode: Literal["w4", "hospital"]
    seed: int
    days: int
    baseline: str
    change: str


class LabDone(Contract):
    state: Literal["done"]
    compare_json: Annotated[str, Field(max_length=524288)]
    compare_txt: Annotated[str, Field(max_length=131072), _clean(_BAD)]  # empty is a service check


class LabRefused(Contract):
    state: Literal["refused"]
    reason: OneLine


class LabFailed(Contract):
    state: Literal["failed"]
    reason: OneLine


class LabAnswer(Contract):
    """A `done` with a record: a patient question's (contract-patient-ask 6.1) or a general
    question's (contract-ask 6.1). The service checks the record against the row's kind."""

    state: Literal["done"]
    record_json: Annotated[str, Field(max_length=131072)]
    answer: Annotated[str, Field(max_length=4000), _clean(_BAD)]  # LF allowed; empty is a service check


def _report_shape(v: Any) -> str | None:
    """`done` has two shapes, told apart by `record_json`. The service checks the shape against the row's kind."""
    if not isinstance(v, dict):
        return "answer" if isinstance(v, LabAnswer) else getattr(v, "state", None)
    state = v.get("state")
    if state == "done" and "record_json" in v:
        return "answer"
    return state if isinstance(state, str) else None


LabReport = Annotated[
    Annotated[LabDone, Tag("done")]
    | Annotated[LabAnswer, Tag("answer")]
    | Annotated[LabRefused, Tag("refused")]
    | Annotated[LabFailed, Tag("failed")],
    Discriminator(_report_shape),
]


class LabRunItem(Contract):
    id: int
    kind: Literal["compare"]
    requested_by: str
    requested_at: str
    mode: Literal["w4", "hospital"]
    seed: int
    days: int
    baseline: str
    change: str
    state: State
    claimed_at: str | None
    finished_at: str | None
    reason: str | None


class LabRunDetail(LabRunItem):
    report: dict[str, Any] | None
    report_text: str | None


class NewPatientQuestion(Contract):
    question: Question  # the patient is the path


class PatientContextDetail(Contract):
    rank: Annotated[int, Field(strict=True, ge=1)]
    score: Annotated[int, Field(strict=True, ge=0, le=20)]
    trend: Trend
    supporting: str
    escalation_reasons: list[EscalationReason]
    vitals: Vitals
    signal_quality: Percent | None
    last_update_s: NonNegNum | None
    vital_history: VitalHistoryFull  # the last 72 hours, gaps kept as null


class PatientContextBaseline(Contract):
    admission_known: Flag
    label: str
    params: BaselineUsed
    vitals: list[
        Annotated[BaselineComputed | BaselineForming | BaselineUnavailable, Field(discriminator="status")]
    ]


class PatientContextAbstained(Contract):
    reason: str
    instruction: str


class PatientContext(Contract):
    """Everything the agent may see of one patient, and nothing else: the allow-list. No name, bed,
    id, free text, note or lab, and nothing of another patient."""

    format: Literal[1]
    synthetic: Flag
    as_of: str
    detail: PatientContextDetail | None
    baseline: PatientContextBaseline | None
    abstained: PatientContextAbstained | None


class PatientQuestionJob(Contract):
    id: int
    kind: Literal["patient_question"]
    question: str
    patient: PatientContext


class PatientQuestionItem(Contract):
    id: int
    requested_by: str
    requested_at: str
    question: str
    state: State
    claimed_at: str | None
    finished_at: str | None
    reason: str | None


class PatientQuestionDetail(PatientQuestionItem):
    answer: str | None
    report: dict[str, Any] | None


class NewQuestion(Contract):
    """A general question about the simulated trials (contract-ask 4): one line, stored as typed."""

    question: Question


class QuestionJob(Contract):
    id: int
    kind: Literal["question"]
    question: str


class QuestionItem(PatientQuestionItem):
    """A general question as the ward's physicians and charge nurses read it."""


class QuestionDetail(QuestionItem):
    answer: str | None
    report: dict[str, Any] | None
