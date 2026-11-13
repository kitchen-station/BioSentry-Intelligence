"""One minimal valid JSON payload per contract model.

Every payload is plain JSON data (what crosses the wire). The tests mutate deep copies to
prove what the schemas reject.
"""

from copy import deepcopy
from typing import Any

from pydantic import BaseModel

from biosentry.schemas import (
    CONTRACT_MODELS,
    AbstainedRow,
    Acknowledgement,
    AcvpuMeasurement,
    AdmitPatientInput,
    BaselineComputed,
    BaselineForming,
    BaselineResponse,
    BaselineUnavailable,
    BaselineUsed,
    BloodPressure,
    BloodPressureMeasurement,
    ClinicalNote,
    EscalationReason,
    FlagMeasurement,
    HistoryEvent,
    HistoryProvenance,
    HistoryRun,
    LabExtractionResult,
    LabPanel,
    LabRunDetail,
    LabRunItem,
    LiveWard,
    OverviewActivity,
    OverviewClinician,
    OverviewPatient,
    OverviewSimCounts,
    OverviewSimStatus,
    MeasurementAbsent,
    NewLabRun,
    NewNote,
    NewPatientQuestion,
    NumberMeasurement,
    NurseVitalsInput,
    PartialAdmitPatientInput,
    PartialLabInput,
    PatientDetail,
    PatientExtractionResult,
    PatientHistory,
    SimulatedEpisode,
    PatientQuestionDetail,
    PatientQuestionItem,
    PatientStay,
    PatientSummary,
    Provenance,
    ModelSignal,
    RowModels,
    ScoreRow,
    Session,
    Spo2ScaleMeasurement,
    TierAScore,
    TierBreakdown,
    TierCInput,
    TierStatus,
    UnscoredPatient,
    VitalHistory,
    VitalHistoryFull,
    Vitals,
    VitalsRecorded,
    VitalTrends,
    WardOverview,
    WardResponse,
)
from biosentry.schemas.common import (
    AcvpuPresent,
    BloodPressurePresent,
    FlagPresent,
    NumberPresent,
    Spo2ScalePresent,
)

NOW = "2026-09-03T07:00:00Z"

LAB_FIELDS = [
    "troponin",
    "ck_mb",
    "bnp",
    "nt_probnp",
    "sodium",
    "potassium",
    "chloride",
    "magnesium",
    "calcium",
    "creatinine",
    "bun",
    "hemoglobin",
    "hematocrit",
    "wbc",
    "platelet_count",
    "pt",
    "inr",
    "sgpt",
    "sgot",
    "blood_glucose",
    "lactate",
]


def provenance(**overrides: Any) -> dict[str, Any]:
    base = {
        "source": "device",
        "tier": None,
        "observed_at": NOW,
        "computed_at": None,
        "model_id": None,
        "synthetic": True,
        "quality": "ok",
    }
    return {**base, **overrides}


FILE_SHA = "ab" * 32


def model_provenance(tier: str, model_id: str) -> dict[str, Any]:
    """A model value names its file and run (variant, file_sha256, run_id)."""
    return provenance(source="model", tier=tier, model_id=model_id, variant="all", file_sha256=FILE_SHA, run_id="m1-x")


def present(value: Any) -> dict[str, Any]:
    return {"present": True, "value": value}


def absent(reason: str = "not_measured") -> dict[str, Any]:
    return {"present": False, "reason": reason}


def measurement(data: dict[str, Any]) -> dict[str, Any]:
    return {"data": data, "provenance": provenance()}


def vitals() -> dict[str, Any]:
    return {
        "heart_rate": measurement(present(118)),
        "respiratory_rate": measurement(present(26)),
        "spo2": measurement(present(91)),
        "temperature": measurement(present(37.2)),
        "blood_pressure": measurement(present({"systolic": 88, "diastolic": 52})),
        "consciousness": measurement(present("A")),
        "supplemental_o2": measurement(absent("not_recorded")),
        "spo2_scale": measurement(absent("not_recorded")),
    }


def vital_trends() -> dict[str, Any]:
    return {k: [1.0, None, 2.0] for k in ("heart_rate", "respiratory_rate", "spo2", "temperature")}


def labs() -> dict[str, Any]:
    panel: dict[str, Any] = dict.fromkeys(LAB_FIELDS)
    panel["bnp"] = measurement(present(1840))
    panel["freshness_hours"] = 1
    return panel


def tier_status() -> dict[str, Any]:
    return {"tier": "A", "live": True, "label": "NEWS2"}


def model_signal() -> dict[str, Any]:
    """A model result that was not computed: the only state that needs no file."""
    return {"state": "not_computed", "reason": "baseline forming", "bin_start": None, "bin_end": None, "provenance": []}


def row_models() -> dict[str, Any]:
    return {"prediction": model_signal(), "alert_policy": model_signal()}


def score_row() -> dict[str, Any]:
    return {
        "id": "p-001",
        "rank": 1,
        "score": 9,
        "trend": "rising",
        "name": "Santos, Maria",
        "age": 67,
        "sex": "F",
        "bed": "4A",
        "admitting_context": "Acute decompensated heart failure",
        "mechanism": "Sorted on NEWS2.",
        "supporting": "HR 118 bpm",
        "vitals": vitals(),
        "vital_trends": vital_trends(),
        "labs": labs(),
        "sort_tier": "A",
        "tier_status": [tier_status()],
        "escalation_reasons": [{"rule": "tier_a_threshold", "text": "NEWS2 >= 7"}],
        "synthetic": True,
        "signal_quality": 94,
        "last_update_s": 4,
        "models": row_models(),
    }


def unscored() -> dict[str, Any]:
    row = score_row()
    keys = (
        "id", "name", "age", "sex", "bed", "admitting_context", "trend", "mechanism",
        "supporting", "vitals", "vital_trends", "labs", "signal_quality",
        "last_update_s", "synthetic",
    )  # fmt: skip
    state = {key: row[key] for key in keys}
    state["admitted_at"] = "2026-09-03T04:00:00Z"
    state["vital_history"] = {
        k: [{"time": "2026-09-03T06:30:00Z", "value": 97}]
        for k in ("heart_rate", "respiratory_rate", "spo2")
    }
    return state


def abstained_row() -> dict[str, Any]:
    return {
        "id": "p-011",
        "name": "Cruz, Ana",
        "age": 41,
        "sex": "F",
        "bed": "7C",
        "admitting_context": "Post-op day 1",
        "reason": "No patch applied",
        "instruction": "Apply patch and verify signal",
        "synthetic": True,
    }


def admit_input() -> dict[str, Any]:
    return {
        "lastName": "Santos",
        "firstName": "Maria",
        "dateOfBirth": "1958-04-02",
        "age": 67,
        "sex": "F",
        "bed": "4A",
        "admissionRoute": "er_triage",
        "chiefComplaint": "Dyspnea",
        "admittingContext": "Acute decompensated heart failure",
        "attendingPhysician": "Dr Reyes",
    }


_BUILDERS: dict[type[BaseModel], Any] = {
    Provenance: provenance,
    BloodPressure: lambda: {"systolic": 120, "diastolic": 80},
    MeasurementAbsent: absent,
    NumberPresent: lambda: present(118),
    NumberMeasurement: lambda: measurement(present(118)),
    FlagPresent: lambda: present(True),
    FlagMeasurement: lambda: measurement(present(True)),
    AcvpuPresent: lambda: present("A"),
    AcvpuMeasurement: lambda: measurement(present("A")),
    Spo2ScalePresent: lambda: present(1),
    Spo2ScaleMeasurement: lambda: measurement(present(1)),
    BloodPressurePresent: lambda: present({"systolic": 120, "diastolic": 80}),
    BloodPressureMeasurement: lambda: measurement(present({"systolic": 120, "diastolic": 80})),
    VitalHistory: lambda: {"time": "2026-09-03T06:30:00Z", "value": 97},
    Vitals: vitals,
    VitalTrends: vital_trends,
    VitalHistoryFull: lambda: {
        k: [{"time": "2026-09-03T06:30:00Z", "value": 97}]
        for k in ("heart_rate", "respiratory_rate", "spo2")
    },
    BaselineUsed: lambda: {"window_bins": 24, "k": 2.0, "p": 3},
    BaselineComputed: lambda: {
        "vital": "heart_rate",
        "status": "computed",
        "baseline_median": 70.0,
        "latest": 90.0,
        "latest_at": "2026-09-03T06:45:00Z",
        "latest_age_s": 120,
        "baseline_from": "2026-09-02T22:00:00Z",
        "anchored_at": "admission",
        "delta": 20.0,
        "z": 3.4,
        "band_low": 58.1,
        "band_high": 81.9,
        "run_bins": 4,
        "since": "2026-09-03T06:00:00Z",
        "significant": True,
        "direction": "rising",
        "pathway": "circulatory",
        "unit": "bpm",
    },
    BaselineForming: lambda: {
        "vital": "spo2",
        "status": "forming",
        "bins_valid": 10,
        "bins_needed": 24,
    },
    BaselineUnavailable: lambda: {
        "vital": "temperature",
        "status": "unavailable",
        "reason": "no_history",
    },
    BaselineResponse: lambda: {
        "patient_id": "p-001",
        "synthetic": True,
        "admission_known": True,
        "label": "Research finding. Synthetic, not validated. Breaks ties between equal NEWS2 totals below a trigger.",
        "params": {"window_bins": 24, "k": 2.0, "p": 3},
        "vitals": [
            {"vital": "spo2", "status": "forming", "bins_valid": 10, "bins_needed": 24},
        ],
    },
    LabPanel: labs,
    PartialLabInput: lambda: {"troponin": 0.04},
    TierStatus: tier_status,
    TierCInput: lambda: {
        "patient_id": "p-001",
        "as_of": NOW,
        "age": 67,
        "sex": "F",
        "admitting_context": "Acute decompensated heart failure",
        "vitals": vitals(),
        "vital_trends": vital_trends(),
        "labs": labs(),
        "synthetic": True,
    },
    EscalationReason: lambda: {
        "rule": "tier_b_finding",
        "text": "KDIGO rising",
        "pathway": "renal",
    },
    ModelSignal: model_signal,
    RowModels: row_models,
    ScoreRow: score_row,
    AbstainedRow: abstained_row,
    WardResponse: lambda: {
        "ward": "4 East",
        "clinician": "RN Dela Cruz",
        "shift": "day",
        "receiving_data": True,
        "sort_tier": "A",
        "tier_status": [tier_status()],
        "ranked": [score_row()],
        "abstained": [abstained_row()],
    },
    TierAScore: lambda: {"score": 9, "label": "NEWS2 9"},
    TierBreakdown: lambda: {
        "tier_a": {"score": 9, "label": "NEWS2 9"},
    },
    PatientDetail: lambda: {
        **score_row(),
        "vital_history": {
            k: [{"time": "2026-09-03T06:30:00Z", "value": 97}]
            for k in ("heart_rate", "respiratory_rate", "spo2")
        },
        "tier_breakdown": {
            "tier_a": {"score": 9, "label": "NEWS2 9"},
        },
    },
    ClinicalNote: lambda: {
        "id": "n-001",
        "patient_id": "p-001",
        "author": "RN Dela Cruz",
        "role": "RN",
        "timestamp": "2026-09-03T06:45:00Z",
        "content": "Patient restless.",
        "synthetic": True,
    },
    Session: lambda: {"clinician_id": "c-01", "ward": "4 East", "role": "rn"},
    Acknowledgement: lambda: {"patient_id": "p-001", "score": 9},
    NewNote: lambda: {"author": "RN Dela Cruz", "role": "RN", "content": "Patient restless."},
    PatientStay: lambda: {
        "id": "p-001",
        "name": "Santos, Maria",
        "bed": "4A",
        "admitted_at": NOW,
        "left_at": None,
        "outcome": None,
        "synthetic": True,
    },
    NewLabRun: lambda: {
        "kind": "compare",
        "mode": "w4",
        "seed": 4,
        "days": 3,
        "baseline": "news2_total_ge_5",
        "change": "news2_chart_trigger",
    },
    LabRunItem: lambda: {
        "id": 1,
        "kind": "compare",
        "requested_by": "md-santos",
        "requested_at": "2026-10-14T12:00:00Z",
        "mode": "w4",
        "seed": 4,
        "days": 3,
        "baseline": "news2_total_ge_5",
        "change": "news2_chart_trigger",
        "state": "queued",
        "claimed_at": None,
        "finished_at": None,
        "reason": None,
    },
    LabRunDetail: lambda: {**_BUILDERS[LabRunItem](), "report": None, "report_text": None},
    NewPatientQuestion: lambda: {"question": "What changed in the last 6 hours?"},
    PatientQuestionItem: lambda: {
        "id": 2,
        "requested_by": "md-santos",
        "requested_at": "2026-10-14T12:00:00Z",
        "question": "What changed in the last 6 hours?",
        "state": "queued",
        "claimed_at": None,
        "finished_at": None,
        "reason": None,
    },
    PatientQuestionDetail: lambda: {**_BUILDERS[PatientQuestionItem](), "answer": None, "report": None},
    HistoryProvenance: lambda: {
        "source": "derived", "tier": "A", "rule": "news2_as_of_bins", "computed_at": NOW, "synthetic": True,
    },
    HistoryRun: lambda: {
        "state": "alert", "start": "2026-09-03T05:00:00Z", "end": "2026-09-03T06:00:00Z", "ongoing": False,
        "bins": 4, "peak_score": 7, "peak_level": "high", "peak_at": "2026-09-03T05:30:00Z", "in_episode": "inside",
        "reasons": ["Total 7 (≥7): high"], "provenance": _BUILDERS[HistoryProvenance](),
    },
    HistoryEvent: lambda: {
        "kind": "acknowledged", "at": "2026-09-03T05:40:00Z", "by": "rn-delacruz", "role": None,
        "score": 7, "note_id": None, "source": "clinician_action",
    },
    SimulatedEpisode: lambda: {
        "kind": "respiratory", "start": "2026-09-03T04:30:00Z", "full": "2026-09-03T05:15:00Z", "source": "simulation",
    },
    PatientHistory: lambda: {
        "patient_id": "p-001", "synthetic": True, "simulated_episode": _BUILDERS[SimulatedEpisode](),
        "label": "Rebuilt from stored 15-minute bins. It can differ from the score shown at the time.",
        "unavailable": None,
        "grid_from": "2026-09-03T00:00:00Z", "grid_to": "2026-09-03T06:00:00Z", "bins": 24,
        "runs": [_BUILDERS[HistoryRun]()], "runs_cut": False,
        "events": [_BUILDERS[HistoryEvent]()], "events_cut": False,
    },
    LiveWard: lambda: {"shared": {"clockS": 90}, "patients": [], "can_control": False},
    OverviewPatient: lambda: {
        "bed": "4A",
        "state": "ranked",
        "score": 5,
        "reason": None,
        "care": ["rn-reyes"],
        "synthetic": True,
    },
    OverviewClinician: lambda: {"id": "rn-reyes", "role": "rn", "patients": 1},
    OverviewActivity: lambda: {
        "clinician_id": "rn-reyes",
        "action": "view_ward",
        "outcome": "ok",
        "count": 2,
    },
    OverviewSimCounts: lambda: {"received": 5, "rejected": 1, "abstained": 2},
    OverviewSimStatus: lambda: {
        "clockS": 90,
        "speed": 10,
        "ended": False,
        "counts": {"received": 5, "rejected": 1, "abstained": 2},
    },
    WardOverview: lambda: {
        "ward": "W4",
        "as_of": "2026-10-14T12:00:00Z",
        "receiving_data": True,
        "synthetic": True,
        "sim": None,
        "patients": [],
        "clinicians": [],
        "activity": [],
    },
    PatientSummary: lambda: {"id": "p-001", "name": "Santos, Maria", "bed": "4A", "ranked": True},
    NurseVitalsInput: lambda: {"systolic": 120, "diastolic": 80},
    VitalsRecorded: lambda: {
        "patient_id": "p-004",
        "heart_rate": None,
        "respiratory_rate": None,
        "spo2": None,
        "temperature": None,
        "blood_pressure": measurement(present({"systolic": 120, "diastolic": 80})),
        "consciousness": None,
        "supplemental_o2": None,
        "spo2_scale": None,
        "ranked": True,
        "rank": 3,
        "score": 4,
        "not_ranked_reason": None,
        "not_ranked_instruction": None,
    },
    UnscoredPatient: lambda: unscored(),
    AdmitPatientInput: admit_input,
    PartialAdmitPatientInput: lambda: {"lastName": "Santos"},
    LabExtractionResult: lambda: {
        "values": {"troponin": 0.04},
        "confidence": {"troponin": 0.94},
        "synthetic": True,
        "model_id": "stub-ocr-0",
    },
    PatientExtractionResult: lambda: {
        "values": {"lastName": "Santos"},
        "confidence": {"lastName": 0.9},
        "synthetic": True,
        "model_id": "stub-ocr-0",
    },
}

assert set(_BUILDERS) == set(CONTRACT_MODELS), (
    "samples.py must define a valid payload for every contract model: "
    f"missing {[m.__name__ for m in set(CONTRACT_MODELS) - set(_BUILDERS)]}"
)


def sample(model: type[BaseModel]) -> dict[str, Any]:
    """A fresh valid payload for `model` (safe to mutate)."""
    return deepcopy(_BUILDERS[model]())
