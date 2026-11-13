"""The admin's ward overview: ids, beds, states, who is assigned and recent activity. No name,
age, sex, vital, lab or note appears here."""

from typing import Literal

from .base import Contract, Flag
from .ingest import View
from .records import SessionRole


class OverviewPatient(Contract):
    id: str
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
    seq: int
    at: str
    clinician_id: str
    action: str
    resource: str
    outcome: str


class WardOverview(Contract):
    ward: str
    as_of: str
    receiving_data: Flag
    synthetic: Flag
    shared: View | None  # the sim's shared view as stored; no patient entries
    patients: list[OverviewPatient]
    clinicians: list[OverviewClinician]
    activity: list[OverviewActivity]
