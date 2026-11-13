"""One patient's stay as a short history. Display only: nothing here feeds the rank, the arbiter or
storage, and no score is stored. Every run is rebuilt on read from the stored 15-minute bins and
the stored observations. A run holds fixed or arbiter texts only; an event holds ids, roles and
times, never a note's text. Times are served time, like the patient's history."""

from typing import Annotated, Final, Literal

from pydantic import AwareDatetime, Field

from .base import Contract, Flag

HISTORY_LABEL: Final = "Rebuilt from stored 15-minute bins. It can differ from the score shown at the time."
Text = Annotated[str, Field(max_length=300)]
Score = Annotated[int, Field(strict=True, ge=0, le=20)]
Id = Annotated[str, Field(max_length=64)]


class HistoryProvenance(Contract):
    source: Literal["derived"]  # rebuilt on read, never stored
    tier: Literal["A"]
    rule: Literal["news2_as_of_bins"]
    computed_at: AwareDatetime  # the server time of this read
    synthetic: Flag


class HistoryRun(Contract):
    state: Literal["alert", "not_ranked", "not_rebuilt", "no_reading"]
    start: AwareDatetime  # the start of the first bin
    end: AwareDatetime  # the end of the last bin
    ongoing: Flag  # the last bin is the latest stored bin of the stay
    bins: Annotated[int, Field(strict=True, ge=1, le=672)]
    peak_score: Score | None  # alert only: the highest NEWS2 total in the run
    peak_level: Literal["single_3", "medium", "high"] | None  # alert only
    peak_at: AwareDatetime | None  # alert only: the start of the peak bin (the earliest on a tie)
    reasons: Annotated[list[Text], Field(max_length=4)]  # the arbiter's texts, or a fixed text
    provenance: HistoryProvenance


class HistoryEvent(Contract):
    kind: Literal["admitted", "acknowledged", "vitals_recorded", "note_added", "bp_charted"]
    at: AwareDatetime
    by: Id | None  # a clinician id: acknowledged and vitals_recorded only
    role: Literal["rn", "md", "charge_rn"] | None  # note_added only, when the stored role is one of these
    score: Score | None  # acknowledged only
    note_id: Id | None  # note_added only
    source: Literal["clinician_action", "clinical_note", "observation", "patient"]


class PatientHistory(Contract):
    patient_id: Id
    synthetic: Flag
    label: Literal["Rebuilt from stored 15-minute bins. It can differ from the score shown at the time."]
    # Why there is no history to show: no bin is stored, the stay holds more bins than the grid, or the
    # four series do not line up. Null when there is one. An empty history with this null is a quiet stay.
    unavailable: Literal["no_bins", "past_cap", "not_aligned"] | None
    grid_from: AwareDatetime | None  # the start of the first bin; null: no history
    grid_to: AwareDatetime | None  # the end of the latest bin
    bins: Annotated[int, Field(strict=True, ge=0, le=672)]
    runs: Annotated[list[HistoryRun], Field(max_length=200)]  # oldest first; the newest 200 kept
    runs_cut: Flag
    events: Annotated[list[HistoryEvent], Field(max_length=300)]  # oldest first; the newest 300 kept
    events_cut: Flag
