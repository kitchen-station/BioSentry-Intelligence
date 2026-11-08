"""Structural rules every contract model must obey (CP1 gate bars)."""

import math
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from biosentry.schemas import CONTRACT_MODELS, INPUT_MODELS
from samples import sample

# Optional-by-design string fields on output models (TS `detail?:` / `pathway?:`).
OPTIONAL_OUTPUT_FIELDS = {("TierBFinding", "detail"), ("EscalationReason", "pathway")}

IDS = [m.__name__ for m in CONTRACT_MODELS]


def _wire_name(model: type[BaseModel], field: str) -> str:
    return model.model_fields[field].alias or field


@pytest.mark.parametrize("model", CONTRACT_MODELS, ids=IDS)
def test_sample_payload_is_valid(model: type[BaseModel]) -> None:
    model.model_validate(sample(model))


REQUIRED = [
    (m, name)
    for m in CONTRACT_MODELS
    for name, info in m.model_fields.items()
    if info.is_required()
]


@pytest.mark.parametrize(
    ("model", "field"), REQUIRED, ids=[f"{m.__name__}.{f}" for m, f in REQUIRED]
)
def test_omitting_a_required_field_is_a_validation_error(
    model: type[BaseModel], field: str
) -> None:
    payload = sample(model)
    del payload[_wire_name(model, field)]
    with pytest.raises(ValidationError) as excinfo:
        model.model_validate(payload)
    assert any(e["type"] == "missing" for e in excinfo.value.errors())


@pytest.mark.parametrize("model", CONTRACT_MODELS, ids=IDS)
def test_unknown_keys_are_rejected(model: type[BaseModel]) -> None:
    payload = sample(model)
    payload["not_in_contract"] = 1
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_no_field_has_a_non_null_default() -> None:
    """0 (or any value) must never stand in for missing (I1)."""
    for model in CONTRACT_MODELS:
        for name, info in model.model_fields.items():
            assert info.default_factory is None, f"{model.__name__}.{name} has a default_factory"
            if not info.is_required():
                assert info.default is None, (
                    f"{model.__name__}.{name} defaults to {info.default!r}; only None is allowed"
                )


def test_defaults_exist_only_on_inputs_and_listed_optional_fields() -> None:
    for model in CONTRACT_MODELS:
        if model in INPUT_MODELS:
            continue
        for name, info in model.model_fields.items():
            if not info.is_required():
                assert (model.__name__, name) in OPTIONAL_OUTPUT_FIELDS, (
                    f"{model.__name__}.{name} is optional; add it to OPTIONAL_OUTPUT_FIELDS "
                    "only if the contract really allows omission"
                )


def test_numeric_fields_on_output_models_are_all_required() -> None:
    for model in CONTRACT_MODELS:
        if model in INPUT_MODELS:
            continue
        for name, info in model.model_fields.items():
            annotation = str(info.annotation)
            if "float" in annotation or "int" in annotation:
                assert info.is_required(), f"{model.__name__}.{name} is numeric with a default"


@pytest.mark.parametrize(
    "bad", ["118", True, None, math.nan, math.inf, {"v": 1}], ids=lambda v: repr(v)
)
def test_numbers_are_strict_and_finite(bad: Any) -> None:
    from biosentry.schemas.common import NumberPresent

    with pytest.raises(ValidationError):
        NumberPresent.model_validate({"present": True, "value": bad})


def test_zero_is_a_real_value_not_a_missing_marker() -> None:
    from biosentry.schemas.common import NumberPresent

    assert NumberPresent.model_validate({"present": True, "value": 0}).value == 0


def test_models_are_frozen() -> None:
    from biosentry.schemas import TierStatus

    status = TierStatus.model_validate(sample(TierStatus))
    with pytest.raises(ValidationError):
        status.label = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"rank": None}, "a ranked patient has a rank and a score"),
        ({"not_ranked_reason": "x"}, "a ranked patient has no NOT RANKED reason"),
        ({"ranked": False}, "a NOT RANKED patient has no rank and no score"),
        (
            {"ranked": False, "rank": None, "score": None, "not_ranked_reason": "x"},
            "a NOT RANKED patient names its reason and instruction",
        ),
    ],
)
def test_a_recorded_vitals_response_is_ranked_or_not_ranked_never_both(
    change: dict[str, Any], error: str
) -> None:
    from biosentry.schemas import VitalsRecorded

    with pytest.raises(ValidationError, match=error):
        VitalsRecorded.model_validate({**sample(VitalsRecorded), **change})


def _sim_patient(pid: str, row_pid: str | None = None) -> dict[str, Any]:
    return {
        "id": pid, "name": "Sim, Pat", "age": 61, "sex": "F", "bed": "9A", "context": "c",
        "story": "s", "rn": "rn-1", "md": None, "trend": "stable", "signal_quality": None,
        "observations": [],
        "bins": [{"patient_id": row_pid or pid, "vital": "heart_rate", "minute": 0, "value": 1.0}],
    }  # fmt: skip


@pytest.mark.parametrize(
    ("patients", "error"),
    [
        ([_sim_patient("s-1", row_pid="s-2")], "carry that patient's id"),
        ([_sim_patient("s-1"), _sim_patient("s-1")], "patient ids must be unique"),
    ],
)
def test_a_sim_run_names_each_patient_once_and_rows_belong_to_their_patient(
    patients: list[dict[str, Any]], error: str
) -> None:
    from biosentry.schemas.ingest import NewRun

    body = {"source": "synthetic", "seed": 1, "origin": "2026-01-01T00:00:00Z"}
    NewRun.model_validate({**body, "patients": [_sim_patient("s-1")]})  # the valid shape
    with pytest.raises(ValidationError, match=error):
        NewRun.model_validate({**body, "patients": patients})
