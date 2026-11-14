"""A patient built from the backend's own models, dumped to the JSON shape the API serves."""

from biosentry.schemas.baseline import BaselineResponse
from biosentry.schemas.rows import AbstainedRow, PatientDetail

T0 = "2026-11-13T08:15:00Z"
PROV = {"source": "device", "tier": None, "observed_at": T0, "computed_at": None, "model_id": None, "synthetic": True,
        "quality": "ok"}

NO_MODEL = {"state": "not_computed", "reason": "no model file", "bin_start": None, "bin_end": None, "provenance": []}


def num(v):
    return {"data": {"present": True, "value": v}, "provenance": PROV}


def absent(kind):
    return {"data": {"present": False, "reason": "not measured"}, "provenance": PROV}


HR = [("2026-11-13T06:00:00Z", 80), ("2026-11-13T06:15:00Z", None), ("2026-11-13T06:30:00Z", 90),
      ("2026-11-13T07:00:00Z", 100), ("2026-11-13T08:15:00Z", 120)]
RR = [("2026-11-13T07:00:00Z", 18), ("2026-11-13T08:15:00Z", 18)]
SP = [("2026-11-13T07:00:00Z", 96), ("2026-11-13T07:30:00Z", 94.5), ("2026-11-13T08:15:00Z", 95)]
LABS = {k: None for k in (
    "troponin ck_mb bnp nt_probnp sodium potassium chloride magnesium calcium creatinine bun hemoglobin hematocrit wbc "
    "platelet_count pt inr sgpt sgot blood_glucose lactate freshness_hours").split()}


DETAIL_KEYS = ("rank", "score", "trend", "supporting", "escalation_reasons", "vitals", "signal_quality", "last_update_s",
               "vital_history")


def series(rows):
    return [{"time": t, "value": v} for t, v in rows]


def detail_dict(**over):
    d = {
        "id": "p-1", "rank": 3, "score": 5, "trend": "rising", "name": "Test Person", "age": 70, "sex": "F",
        "bed": "B4", "admitting_context": "ward", "mechanism": "m", "supporting": "NEWS2 5 and the heart rate is rising.",
        "vitals": {"heart_rate": num(120), "respiratory_rate": num(18), "spo2": num(95), "temperature": num(37.2),
                   "blood_pressure": absent("bp"), "consciousness": absent("c"), "supplemental_o2": absent("o"),
                   "spo2_scale": absent("s")},
        "vital_trends": {"heart_rate": [120], "respiratory_rate": [18], "spo2": [95], "temperature": [37.2]},
        "labs": LABS, "sort_tier": "A", "tier_status": [], "models": {"prediction": NO_MODEL, "alert_policy": NO_MODEL},
        "escalation_reasons": [{"rule": "tier_a_threshold", "text": "NEWS2 total 5 reached the trigger.", "pathway": None},
                               {"rule": "tier_b_finding", "text": "Heart rate 40 over baseline.", "pathway": "cardiac"}],
        "synthetic": True, "signal_quality": 90, "last_update_s": 12,
        "vital_history": {"heart_rate": series(HR), "respiratory_rate": series(RR), "spo2": series(SP)},
        "tier_breakdown": {"tier_a": None}, "now": T0,
        }
    full = PatientDetail.model_validate(d).model_dump(mode="json")
    cut = {k: full[k] for k in DETAIL_KEYS}  # the allow-list of contract-patient-ask section 5
    return {**cut, **over}  # an override may be hostile or an extra key: not validated


def baseline_dict(vitals=None):
    base = [{"vital": "heart_rate", "status": "computed", "baseline_median": 72.5, "latest": 120, "latest_at": T0,
             "latest_age_s": 60, "baseline_from": "2026-11-12T20:00:00Z", "anchored_at": "admission", "delta": 47.5,
             "z": 4.2, "band_low": 61.5, "band_high": 83.5, "run_bins": 3, "since": "2026-11-13T07:30:00Z", "significant": True, "direction": "rising",
             "pathway": None, "unit": "bpm"},
            {"vital": "respiratory_rate", "status": "forming", "bins_valid": 10, "bins_needed": 24},
            {"vital": "spo2", "status": "unavailable", "reason": "patch_not_live"}]
    full = BaselineResponse.model_validate({
        "patient_id": "p-1", "synthetic": True, "admission_known": True,
        "label": "Research finding. Synthetic, not validated. Breaks ties between equal NEWS2 totals below a trigger.",
        "params": {"window_bins": 24, "k": 2.0, "p": 3}, "vitals": vitals or base}).model_dump(mode="json")
    return {k: v for k, v in full.items() if k not in ("patient_id", "synthetic")}


def patient(**over):
    return {"format": 1, "synthetic": True, "as_of": T0, "detail": detail_dict(), "baseline": baseline_dict(),
            "abstained": None, **over}


def abstained():
    row = AbstainedRow.model_validate({
        "id": "p-1", "name": "T", "age": 70, "sex": "F", "bed": "B4", "admitting_context": "ward",
        "reason": "SpO2 is missing.", "instruction": "Take a new SpO2 reading.", "synthetic": True}).model_dump(mode="json")
    return {"format": 1, "synthetic": True, "as_of": T0, "detail": None, "baseline": None,
            "abstained": {"reason": row["reason"], "instruction": row["instruction"]}}
