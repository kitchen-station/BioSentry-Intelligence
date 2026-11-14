"""The allow-list of a patient question (contract-patient-ask section 5): which keys the context
holds at each level, and that nothing else can reach it. No database."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError
from samples import sample  # isort: skip

from biosentry.schemas import (
    AbstainedRow,
    BaselineComputed,
    BaselineForming,
    BaselineResponse,
    BaselineUnavailable,
    PatientDetail,
)
from biosentry.schemas.lab import LabAnswer, LabReport
from biosentry.services.patient_ask import build, canonical

AS_OF = datetime(2026, 9, 3, 7, 0, tzinfo=UTC)
TOP = {"format", "synthetic", "as_of", "detail", "baseline", "abstained"}
DETAIL = {
    "rank", "score", "trend", "supporting", "escalation_reasons", "vitals",
    "signal_quality", "last_update_s", "vital_history",
}  # fmt: skip
VITALS = {
    "heart_rate", "respiratory_rate", "spo2", "temperature", "blood_pressure",
    "consciousness", "supplemental_o2", "spo2_scale",
}  # fmt: skip
BASELINE = {"admission_known", "label", "params", "vitals"}


def detail(**over: Any) -> PatientDetail:
    return PatientDetail.model_validate({**sample(PatientDetail), **over})


def baseline() -> BaselineResponse:
    return BaselineResponse.model_validate(sample(BaselineResponse))


def test_the_context_holds_exactly_the_listed_keys_at_every_level() -> None:
    context = build(detail(), baseline(), AS_OF).model_dump(mode="json")
    assert set(context) == TOP
    assert set(context["detail"]) == DETAIL
    assert set(context["detail"]["vitals"]) == VITALS
    assert set(context["detail"]["vital_history"]) == {"heart_rate", "respiratory_rate", "spo2"}
    assert set(context["baseline"]) == BASELINE
    assert set(context["baseline"]["params"]) == {"window_bins", "k", "p"}
    assert context["abstained"] is None and context["format"] == 1 and context["as_of"] == "2026-09-03T07:00:00Z"


PROVENANCE = {"source", "tier", "observed_at", "computed_at", "model_id", "quality", "synthetic"}
# Every key path of the context, from contract-patient-ask section 5. `*` is a vital's name, `[]` a list.
PATHS = (
    {"format", "synthetic", "as_of", "abstained"}
    | {f"detail.{k}" for k in ("rank", "score", "trend", "supporting", "signal_quality", "last_update_s")}
    | {f"detail.escalation_reasons[].{k}" for k in ("rule", "text", "pathway")}
    | {f"detail.vitals.*.data.{k}" for k in ("present", "value", "reason", "value.systolic", "value.diastolic")}
    | {f"detail.vitals.*.provenance.{k}" for k in PROVENANCE}
    | {f"detail.vital_history.*[].{k}" for k in ("time", "value")}
    | {"baseline.admission_known", "baseline.label"}
    | {f"baseline.params.{k}" for k in ("window_bins", "k", "p")}
    | {
        f"baseline.vitals[].{k}"
        for k in (
            "vital", "status", "baseline_median", "latest", "latest_at", "latest_age_s", "baseline_from",
            "anchored_at", "delta", "z", "run_bins", "since", "significant", "direction", "pathway", "unit",
            "bins_valid", "bins_needed", "reason",
        )  # computed, forming and unavailable together
    }  # fmt: skip
)


def paths(value: Any, at: str = "") -> set[str]:
    if isinstance(value, dict):
        names = at in ("detail.vitals", "detail.vital_history")  # their keys are vital names
        return set().union(*(paths(v, f"{at}.{'*' if names else k}".lstrip(".")) for k, v in value.items()))
    if isinstance(value, list):
        return set().union(*(paths(v, at + "[]") for v in value))
    return {at}


def test_every_key_path_of_the_context_is_one_the_contract_lists() -> None:
    """A field added to a nested model (a measurement's provenance, a reason, a baseline finding)
    would reach the context and its hash. This fails then: widen the contract first, or leave it out."""
    vitals = [sample(BaselineComputed), sample(BaselineForming), sample(BaselineUnavailable)]
    full = BaselineResponse.model_validate({**sample(BaselineResponse), "vitals": vitals})
    context = build(detail(), full, AS_OF).model_dump(mode="json")
    assert paths(context) == PATHS


def test_a_not_ranked_patient_has_the_abstained_form_only() -> None:
    row = AbstainedRow.model_validate(sample(AbstainedRow))
    context = build(row, None, AS_OF).model_dump(mode="json")
    assert set(context) == TOP
    assert (context["detail"], context["baseline"]) == (None, None)
    assert context["abstained"] == {"reason": row.reason, "instruction": row.instruction}


def test_a_ranked_patient_may_have_no_baseline() -> None:
    assert build(detail(), None, AS_OF).baseline is None


def test_a_field_added_to_the_source_never_reaches_the_context() -> None:
    class Wider(PatientDetail):
        secret: str

    before = build(detail(), baseline(), AS_OF)
    wider = Wider.model_validate({**sample(PatientDetail), "secret": "MARKER-NEW-FIELD"})
    after = build(wider, baseline(), AS_OF)
    assert canonical(after) == canonical(before)
    assert "MARKER-NEW-FIELD" not in canonical(after)[0]


def test_names_beds_ids_text_and_the_tier_values_are_not_copied() -> None:
    markers = {
        "name": "MARKER-NAME", "bed": "MARKER-BED", "id": "MARKER-ID",
        "admitting_context": "MARKER-CONTEXT", "mechanism": "MARKER-MECHANISM",
    }  # fmt: skip
    found = detail(**markers)
    text, _ = canonical(build(found, baseline(), AS_OF))
    assert "MARKER" not in text
    for key in ("tier_b", "tier_c", "tier_breakdown", "tier_status", "sort_tier", "labs", "vital_trends"):
        assert key not in text


def test_the_history_is_the_last_72_hours_with_gaps_kept() -> None:
    edge = AS_OF - timedelta(hours=72)

    def bins(values: list[float | None]) -> list[dict[str, Any]]:
        times = [edge - timedelta(minutes=15), edge, edge + timedelta(minutes=15), AS_OF]
        return [{"time": t.isoformat().replace("+00:00", "Z"), "value": v} for t, v in zip(times, values, strict=True)]

    history = {
        "heart_rate": bins([70, 71, None, 73]),
        "respiratory_rate": bins([None, None, None, None]),
        "spo2": bins([95, 96, 97, 98]),
    }
    got = build(detail(vital_history=history), None, AS_OF).model_dump(mode="json")["detail"]["vital_history"]
    assert [b["value"] for b in got["heart_rate"]] == [71, None, 73]  # the bin at the edge stays
    assert [b["value"] for b in got["respiratory_rate"]] == [None, None, None]
    assert len(got["spo2"]) == 3


def test_the_hash_is_the_documented_expression_on_the_parsed_text() -> None:
    import hashlib

    text, sha = canonical(build(detail(), baseline(), AS_OF))
    parsed = json.loads(text)
    again = json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    assert again == text
    assert sha == hashlib.sha256(again.encode("utf-8")).hexdigest()


def test_a_report_that_is_not_a_body_has_no_shape() -> None:
    adapter = TypeAdapter(LabReport)
    answer = LabAnswer(state="done", record_json="{}", answer="x")
    assert adapter.validate_python(answer) is answer
    for value in ([], "done", 3):
        with pytest.raises(ValidationError, match="union_tag_not_found"):
            adapter.validate_python(value)
