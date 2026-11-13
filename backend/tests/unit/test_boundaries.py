"""Small boundaries: settings from the environment, the wall clock, the blood pressure parser and
the ward row's patch-signal guard."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from biosentry.core.audit import utc_now
from biosentry.core.config import DEFAULT_DATABASE_URL, Settings
from biosentry.domain.arbiter import Ranked
from biosentry.schemas import UnscoredPatient
from biosentry.services.patients import RejectedError, split_blood_pressure
from biosentry.services.scoring import decide
from biosentry.services.ward import score_row
from samples import measurement, present, unscored  # isort: skip


def test_settings_read_the_environment_and_an_empty_value_is_unset() -> None:
    url = "postgresql://u@db.example/x"
    given = Settings.from_env({"BIOSENTRY_DATABASE_URL": url, "BIOSENTRY_INGEST_KEY": "k"})
    assert (given.database_url, given.ingest_key) == (url, "k")
    empty = Settings.from_env({"BIOSENTRY_DATABASE_URL": "", "BIOSENTRY_INGEST_KEY": ""})
    assert (empty.database_url, empty.ingest_key) == (DEFAULT_DATABASE_URL, None)


def test_settings_default_to_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIOSENTRY_INGEST_KEY", "from-env")
    assert Settings.from_env().ingest_key == "from-env"


def test_the_wall_clock_is_utc() -> None:
    before = datetime.now(UTC)
    now = utc_now()
    assert now.tzinfo is UTC and before <= now <= datetime.now(UTC)


@pytest.mark.parametrize("text", ["120", "120/", "/80", "a/80", "120/8o", "-1/80"])
def test_a_blood_pressure_that_is_not_systolic_slash_diastolic_is_rejected(text: str) -> None:
    with pytest.raises(RejectedError, match="systolic/diastolic"):
        split_blood_pressure(text)


def test_a_ranked_row_may_have_no_patch_signal() -> None:
    payload = unscored()
    payload["vitals"]["supplemental_o2"] = measurement(present(False))
    payload["vitals"]["spo2_scale"] = measurement(present(1))
    state = UnscoredPatient.model_validate({**payload, "signal_quality": None})
    decision = decide(state)
    assert isinstance(decision, Ranked)
    assert score_row(state, 1, decision, []).signal_quality is None


SRC = Path(__file__).resolve().parents[2] / "src" / "biosentry"
PROCESS = re.compile(
    r"subprocess|multiprocessing|posix_spawn|\bfork\b|os\.(system|exec|spawn|popen)|from os import"
    r"|create_subprocess|\bpty\b|ctypes|__import__|import_module"
)


def test_the_api_never_starts_a_process() -> None:
    files = list(SRC.rglob("*.py"))
    assert len(files) > 30  # the walk is not vacuous
    assert [str(f) for f in files if PROCESS.search(f.read_text("utf-8"))] == []


def test_nothing_that_ranks_a_patient_reads_a_lab_run() -> None:
    names = ("ward.py", "scoring.py", "patients.py", "baseline.py")
    ranking = [SRC / "services" / n for n in names] + [SRC / "repositories" / "postgres.py"]
    ranking += list((SRC / "domain").rglob("*.py"))
    assert len(ranking) > 5
    pattern = re.compile(r"lab_run|biosentry\.(services|schemas)\.lab\b|import[^\n]*\blab\b")
    assert [str(f) for f in ranking if pattern.search(f.read_text("utf-8"))] == []
