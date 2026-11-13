"""Migrations: each runs once, in order; a database built before migrations is adopted as it is; a
failing migration changes nothing; misnamed or renumbered files are refused before anything runs."""

import shutil
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import psycopg
import pytest

from biosentry.db.migrate import MIGRATIONS, migrate, migrations
from biosentry.db.seed import ensure
from biosentry.repositories.postgres import connect

INITIAL = MIGRATIONS / "0001_initial.sql"


def _names(url: str) -> list[str]:
    with connect(url) as conn:
        return [r["name"] for r in conn.execute("SELECT name FROM schema_migration ORDER BY name")]


def test_every_migration_is_recorded_once(database_url: str) -> None:
    assert {"0010_lab_run", "0011_patient_question", "0012_sim_episode"} <= set(_names(database_url))
    assert _names(database_url) == [p.stem for p in migrations()]
    ensure(database_url)  # a restart
    with connect(database_url) as conn:
        assert migrate(conn) == []
    assert _names(database_url) == [p.stem for p in migrations()]


def test_a_database_built_before_migrations_is_adopted_with_its_rows(blank_database_url: str) -> None:
    with connect(blank_database_url) as conn:  # what schema.sql used to build on start
        conn.execute(INITIAL.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO clinician (id, role) VALUES ('rn-old', 'rn')")
    ensure(blank_database_url)
    with connect(blank_database_url) as conn:
        kept = conn.execute("SELECT role FROM clinician WHERE id = 'rn-old'").fetchone()
    assert kept == {"role": "rn"}
    assert _names(blank_database_url)[0] == "0001_initial"


def test_a_failing_migration_changes_nothing(blank_database_url: str, tmp_path: Path) -> None:
    shutil.copy(INITIAL, tmp_path)
    (tmp_path / "0002_half_done.sql").write_text(
        "CREATE TABLE half_done (id INT); SELECT 1 / 0;", encoding="utf-8"
    )
    with connect(blank_database_url) as conn:
        with pytest.raises(psycopg.errors.DivisionByZero):
            migrate(conn, tmp_path)
        tables = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    assert tables == []  # not even 0001 or schema_migration: one transaction


@pytest.mark.parametrize(
    ("files", "error"),
    [
        (["0001_initial.sql", "2_short.sql"], "NNNN_what_it_does.sql: 2_short.sql"),
        (["0001_initial.sql", "0002_Upper.sql"], "0002_Upper.sql"),
        (["0001_initial.sql", "0002_a.sql", "0002_b.sql"], "used twice: 0002"),
    ],
)
def test_misnamed_or_renumbered_migrations_are_refused(
    tmp_path: Path, files: list[str], error: str
) -> None:
    for name in files:
        (tmp_path / name).write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        migrations(tmp_path)


def _run_with_a_patient(conn: psycopg.Connection[Any], source: str, synthetic: bool) -> int:
    row = conn.execute(
        "INSERT INTO run (source, synthetic, ward, origin, as_of, tier_status)"
        " VALUES (%s, %s, 'W4', now(), now(), '[]') RETURNING id",
        (source, synthetic),
    ).fetchone()
    assert row is not None
    run_id = int(row["id"])
    conn.execute(
        "INSERT INTO patient (run_id, id, name, bed, admitting_context, mechanism, supporting, trend)"
        " VALUES (%s, 'x-1', 'n', 'b', 'c', 'm', '', 'stable')",
        (run_id,),
    )
    conn.execute(
        "INSERT INTO observation (run_id, patient_id, code, num, observed_at, source, quality,"
        " synthetic) VALUES (%s, 'x-1', 'heart_rate', 80, now(), 'device', 'ok', %s)",
        (run_id, synthetic),
    )
    conn.execute(
        "INSERT INTO sim_event (run_id, seq, minute, kind, text) VALUES (%s, 1, 0, 'k', 't')",
        (run_id,),
    )
    return run_id


def test_dropping_the_eicu_replay_removes_its_runs_and_keeps_everything_else(
    blank_database_url: str, tmp_path: Path
) -> None:
    shutil.copy(INITIAL, tmp_path)
    with connect(blank_database_url) as conn:
        migrate(conn, tmp_path)  # the database as it stood with 0001 only
        replay = _run_with_a_patient(conn, "eicu_demo", False)
        kept = _run_with_a_patient(conn, "synthetic", True)
        conn.execute(
            "INSERT INTO audit_log (at, action, resource, outcome)"
            " VALUES (now(), 'view_ward', 'W4', 'ok')"
        )
        assert "0002_drop_eicu_replay" in migrate(conn)
        for table in ("run", "patient", "observation", "sim_event"):
            column = "id" if table == "run" else "run_id"
            rows = conn.execute(f"SELECT {column} AS id FROM {table}").fetchall()
            assert {r["id"] for r in rows} == {kept}, table  # the replay's rows cascaded away
        assert replay != kept
        assert conn.execute("SELECT count(*) AS n FROM audit_log").fetchone() == {"n": 1}
        for source, synthetic in (("eicu_demo", False), ("eicu_demo", True), ("synthetic", False)):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO run (source, synthetic, ward, origin, as_of, tier_status)"
                    " VALUES (%s, %s, 'W4', now(), now(), '[]')",
                    (source, synthetic),
                )
        for source in ("fixture", "synthetic"):  # what stays allowed
            conn.execute(
                "INSERT INTO run (source, synthetic, ward, origin, as_of, tier_status)"
                " VALUES (%s, TRUE, 'W4', now(), now(), '[]')",
                (source,),
            )


def test_a_session_from_the_stub_sign_in_does_not_survive_the_passphrase_migration(
    blank_database_url: str, tmp_path: Path
) -> None:
    from biosentry.repositories.postgres import PostgresStore

    for name in ("0001_initial.sql", "0002_drop_eicu_replay.sql"):
        shutil.copy(MIGRATIONS / name, tmp_path)
    token = "stub-era-token"
    with connect(blank_database_url) as conn:
        migrate(conn, tmp_path)  # the database as it stood before real sign-in
        conn.execute("INSERT INTO clinician (id, role) VALUES ('rn-old', 'rn')")
        conn.execute(
            "INSERT INTO session (token_hash, clinician_id, created_at, expires_at)"
            " VALUES (%s, 'rn-old', now(), now() + interval '12 hours')",
            (sha256(token.encode()).hexdigest(),),
        )
        assert conn.execute("SELECT count(*) AS n FROM session").fetchone() == {"n": 1}
    ensure(blank_database_url)  # migrates on, and seeds the fixture run
    with connect(blank_database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM session").fetchone() == {"n": 0}
        assert PostgresStore(conn, lambda: datetime.now(UTC)).clinician_for(token) is None
        kept = conn.execute("SELECT passphrase_hash FROM clinician WHERE id = 'rn-old'").fetchone()
    assert kept == {"passphrase_hash": None}  # the clinician stays; only the session went


def test_a_compare_run_from_before_the_patient_question_survives_with_the_new_columns_empty(
    blank_database_url: str, tmp_path: Path
) -> None:
    for path in migrations():
        if path.stem < "0011":
            shutil.copy(path, tmp_path)
    insert = (
        "INSERT INTO lab_run (ward, requested_by, requested_at, kind, mode, seed, days, baseline,"
        " change, state{extra}) VALUES ('W4', 'md-old', now(), 'compare', 'w4', 1, 1,"
        " 'news2_total_ge_5', 'news2_chart_trigger', {values})"
    )
    with connect(blank_database_url) as conn:
        migrate(conn, tmp_path)
        conn.execute("INSERT INTO clinician (id, role) VALUES ('md-old', 'md')")
        conn.execute(insert.format(extra="", values="'queued'"))
        conn.execute(
            insert.format(
                extra=", claimed_at, finished_at, report, report_text",
                values="'done', now(), now(), '{\"a\": 1}', 't'",
            )
        )
        before = conn.execute("SELECT id, state, mode, report, report_text FROM lab_run ORDER BY id").fetchall()
        assert migrate(conn) == ["0011_patient_question", "0012_sim_episode"]
        after = conn.execute("SELECT * FROM lab_run ORDER BY id").fetchall()
        names = {
            r["conname"]
            for r in conn.execute(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'lab_run'::regclass"
            )
        }
    assert [{k: r[k] for k in before[0]} for r in after] == before
    for r in after:
        for column in ("question", "run_id", "patient_id", "context", "context_sha256"):
            assert r[column] is None
    assert {"lab_run_kind_check", "lab_run_fields_of_kind", "lab_run_patient"} <= names
