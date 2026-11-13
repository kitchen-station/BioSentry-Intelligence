"""The admin's ward overview: beds, states, who is assigned and counts of recent activity. No
patient id, name, age, sex, vital, lab or note appears here."""

from typing import Literal

from pydantic import Field, field_validator

from .base import Contract, Flag
from .records import SessionRole


class OverviewPatient(Contract):
    bed: str
    state: Literal["ranked", "not_ranked"]
    score: int | None  # the NEWS2 total when ranked
    reason: str | None  # the arbiter's NOT RANKED reason: names missing inputs, no values
    care: list[str]  # clinician ids assigned, sorted; includes the charge nurse
    synthetic: Flag


class OverviewClinician(Contract):
    id: str
    role: SessionRole
    patients: int  # patients on the ward assigned to this clinician


class OverviewActivity(Contract):
    """How many times a clinician did one action with one outcome in the last day. Counts, not
    rows: a row would show which patient a named clinician opened and when. Oversight needs
    volume and failures."""

    clinician_id: str
    action: str
    outcome: str
    count: int = Field(ge=1)


class OverviewSimCounts(Contract):
    received: int = Field(ge=0)
    rejected: int = Field(ge=0)
    abstained: int = Field(ge=0)


class OverviewSimStatus(Contract):
    """Only what the status line reads from the sim's shared view; nothing else is copied."""

    clockS: float = Field(ge=0, strict=True)
    speed: Literal[0, 1, 10, 60]
    ended: bool = Field(strict=True)
    counts: OverviewSimCounts

    @field_validator("speed", mode="before")
    @classmethod
    def _not_a_bool(cls, value: object) -> object:
        if isinstance(value, bool):  # True would read as 1
            raise ValueError("speed is a number")
        return value


class WardOverview(Contract):
    ward: str
    as_of: str
    receiving_data: Flag
    synthetic: Flag
    sim: OverviewSimStatus | None  # None: nothing published, or the stored view is not as read
    patients: list[OverviewPatient]
    clinicians: list[OverviewClinician]
    activity: list[OverviewActivity]
