"""Schema migrations: numbered SQL files in `migrations/`, each applied once, in order.

    python -m biosentry.db.migrate [--url DATABASE_URL]     (make db-migrate)

A migration is `NNNN_what_it_does.sql`, four digits, the next free number. Once merged it is never
edited: change the schema with a new file. `schema_migration` records what ran. The API runs this
on start (`seed.ensure`), with the seed, in one transaction behind the setup lock, so a failed
migration leaves the database as it was and two processes starting at once never both apply one.
"""

import argparse
import re
from pathlib import Path

from biosentry.core.config import Settings
from biosentry.repositories.postgres import Conn, connect, transaction

MIGRATIONS = Path(__file__).with_name("migrations")
# One key for "this database is being set up": two API processes starting at once take turns, so
# neither trips over the other's CREATE or seeds the fixture twice. (Run locks use small run ids.)
SETUP_LOCK = 0x62696F7365656400
_NAME = re.compile(r"(\d{4})_[a-z0-9_]+\.sql")


def migrations(directory: Path = MIGRATIONS) -> list[Path]:
    """Every migration, in the order it runs. A misnamed file or a reused number is an error."""
    paths = sorted(directory.glob("*.sql"))
    bad = [p.name for p in paths if not _NAME.fullmatch(p.name)]
    if bad:
        raise ValueError(f"migration names must be NNNN_what_it_does.sql: {', '.join(bad)}")
    numbers = [p.name[:4] for p in paths]
    reused = sorted({n for n in numbers if numbers.count(n) > 1})
    if reused:  # two branches that both took the next number
        raise ValueError(f"migration number used twice: {', '.join(reused)}")
    return paths


def migrate(conn: Conn, directory: Path = MIGRATIONS) -> list[str]:
    """Apply the migrations not yet recorded, oldest first; returns their names.

    Runs in the caller's transaction when there is one (`seed.ensure` holds the setup lock).
    """
    # ponytail: every migration runs inside one transaction, so a statement PostgreSQL refuses
    # there (CREATE INDEX CONCURRENTLY, ALTER TYPE ... ADD VALUE before 12) cannot be a migration.
    # Upgrade: a per-file "no transaction" marker when the first such migration is needed.
    paths = migrations(directory)
    with transaction(conn):
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migration ("
            " name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {r["name"] for r in conn.execute("SELECT name FROM schema_migration")}
        applied = []
        for path in paths:
            if path.stem in done:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migration (name) VALUES (%s)", (path.stem,))
            applied.append(path.stem)
    return applied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=Settings.from_env().database_url)
    args = parser.parse_args(argv)
    with connect(args.url) as conn, transaction(conn):
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (SETUP_LOCK,))  # as the API takes on start
        applied = migrate(conn)
    print("\n".join(applied) if applied else "up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
