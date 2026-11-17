"""Rhythm is an ingest code with its own two values; a letter of ACVPU is not one of them."""

import pytest
from pydantic import ValidationError

from biosentry.schemas.ingest import Obs


def obs(code: str, txt: str, source: str = "nurse") -> Obs:
    return Obs.model_validate(
        {"patient_id": "p-1", "code": code, "minute": -1, "txt": txt, "source": source, "quality": "ok"}
    )


@pytest.mark.parametrize("value", ["sinus", "af"])
def test_ingest_accepts_rhythm(value: str) -> None:
    assert obs("rhythm", value).txt == value


@pytest.mark.parametrize(("code", "txt"), [("rhythm", "A"), ("rhythm", "U"), ("consciousness", "af"), ("consciousness", "sinus")])
def test_ingest_refuses_a_value_of_the_other_code(code: str, txt: str) -> None:
    with pytest.raises(ValidationError):
        obs(code, txt)
