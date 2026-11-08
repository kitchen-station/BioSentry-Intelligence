"""Database fixtures. A template database is seeded once per session; each test that asks for
`database_url` gets its own copy (CREATE DATABASE ... TEMPLATE), dropped afterwards. Tests that
never ask (unit, contract) need no PostgreSQL.

The server is the one `make db-up` starts, or whatever BIOSENTRY_DATABASE_URL names. Its role
must be allowed to create databases. Test databases are named bs_test_* and live beside the dev database.
"""

import os
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import tuple_row

from biosentry.core.config import DEFAULT_DATABASE_URL
from biosentry.db.seed import ensure

BASE_URL = os.environ.get("BIOSENTRY_DATABASE_URL") or DEFAULT_DATABASE_URL


def _url(name: str) -> str:
    return make_conninfo(BASE_URL, dbname=name)


def _admin() -> psycopg.Connection[Any]:
    try:
        return psycopg.connect(_url("postgres"), autocommit=True, connect_timeout=5)
    except psycopg.OperationalError as error:
        pytest.fail(
            f"these tests need PostgreSQL and it is not reachable ({error}); run `make db-up`",
            pytrace=False,
        )


def _drop(admin: psycopg.Connection[Any], name: str) -> None:
    # FORCE: a test may leave a connection of its own open.
    admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def dump_database(conn: psycopg.Connection[Any]) -> str:
    """Every row of every table as text, for the checks that nothing sensitive was stored."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        tables = [r[0] for r in cur.fetchall()]
        lines = []
        for table in tables:
            cur.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table)))
            lines += [f"{table}: {row}" for row in cur.fetchall()]
    return "\n".join(lines)


@pytest.fixture(scope="session")
def template_database() -> Iterator[str]:
    name = f"bs_test_template_{uuid.uuid4().hex[:12]}"
    with _admin() as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            ensure(_url(name))
            yield name
        finally:
            _drop(admin, name)


@pytest.fixture
def database_url(template_database: str) -> Iterator[str]:
    """A seeded database of this test's own."""
    name = f"bs_test_{uuid.uuid4().hex[:12]}"
    with _admin() as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(name), sql.Identifier(template_database)
            )
        )
        try:
            yield _url(name)
        finally:
            _drop(admin, name)


@pytest.fixture
def blank_database_url() -> Iterator[str]:
    """An empty database (no schema, no seed), for a test that seeds its own dataset."""
    name = f"bs_test_{uuid.uuid4().hex[:12]}"
    with _admin() as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            yield _url(name)
        finally:
            _drop(admin, name)
