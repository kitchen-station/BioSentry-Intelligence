"""The ways the backend is started: the ASGI module, and the migrate, seed and contract commands,
called as functions and run as `python -m`."""

import importlib
import runpy
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI

from biosentry.db import migrate, seed
from biosentry.repositories.postgres import connect
from biosentry.schemas import openapi


def _count(url: str, table: str) -> int:
    with connect(url) as conn:
        row = conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()
    assert row is not None
    return int(row["n"])


def test_the_asgi_module_builds_the_app_from_the_environment(
    blank_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BIOSENTRY_DATABASE_URL", blank_database_url)
    monkeypatch.delitem(sys.modules, "biosentry.api.main", raising=False)
    main = importlib.import_module("biosentry.api.main")
    assert isinstance(main.app, FastAPI)
    assert _count(blank_database_url, "run") == 1  # start-up migrated and seeded the database


def test_migrate_applies_then_reports_up_to_date(
    blank_database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert migrate.main(["--url", blank_database_url]) == 0
    assert capsys.readouterr().out.split() == [p.stem for p in migrate.migrations()]
    assert migrate.main(["--url", blank_database_url]) == 0
    assert capsys.readouterr().out == "up to date\n"


def test_seed_sets_up_once_and_reset_starts_over(database_url: str) -> None:
    with connect(database_url) as conn:
        conn.execute("INSERT INTO clinician (id, role) VALUES ('rn-extra', 'rn')")
    assert seed.main(["--url", database_url]) == 0  # already seeded: nothing changes
    assert _count(database_url, "run") == 1 and _count(database_url, "clinician") > 0
    before = _count(database_url, "clinician")
    assert seed.main(["--url", database_url, "--reset"]) == 0
    assert _count(database_url, "clinician") == before - 1  # the extra one went with the reset
    assert _count(database_url, "run") == 1


def test_the_contract_command_writes_the_openapi_document(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "openapi.json"
    assert openapi.main(["--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == openapi.dumps(openapi.build_openapi())


@pytest.mark.filterwarnings("ignore:.*found in sys.modules:RuntimeWarning")
@pytest.mark.parametrize(
    ("module", "args"),
    [
        ("biosentry.db.migrate", ["--url", "{url}"]),
        ("biosentry.db.seed", ["--url", "{url}"]),
        ("biosentry.schemas.openapi", ["--out", "{tmp}/openapi.json"]),
    ],
)
def test_each_command_runs_as_a_module_and_exits_zero(
    module: str,
    args: list[str],
    blank_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv = [a.format(url=blank_database_url, tmp=tmp_path) for a in args]
    monkeypatch.setattr(sys, "argv", [module, *argv])
    with pytest.raises(SystemExit) as exited:
        runpy.run_module(module, run_name="__main__")
    assert exited.value.code == 0
