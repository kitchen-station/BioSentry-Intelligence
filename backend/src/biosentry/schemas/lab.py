"""Bodies of the Simulation lab (contract-d7): a clinician's request, the job the lab worker claims,
the worker's report, and what a clinician reads back. Every value of a request comes from the fixed
lists below, so a request holds no path, no free text and no patient."""

import re
from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, Field, model_validator

from .base import Contract

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


LabReport = Annotated[LabDone | LabRefused | LabFailed, Field(discriminator="state")]


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
