"""Migrations: each runs once, in order; a database built before migrations is adopted as it is; a
failing migration changes nothing; misnamed or renumbered files are refused before anything runs."""

import shutil
from pathlib import Path

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
