"""In production nothing connects with the dev database password, however the URL, PGPASSWORD or a
passfile got it there, at every entry point that connects; and the public dev passphrase is never
seeded, whatever the host. The messages never hold a URL or a secret."""

import io
import sys
from typing import Any

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from biosentry.api.app import create_app
from biosentry.core.config import DEV_DATABASE_PASSWORD, ConfigError, Settings, restricted
from biosentry.db import migrate, seed
from biosentry.db import passphrase as passphrase_cli
from biosentry.repositories import postgres
from biosentry.repositories.postgres import connect

KEY = "abcdefghijklmnop" * 2  # a production-grade ingest key


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIOSENTRY_ENV", "production")


def _uri(url: str, password: str | None = None) -> str:
    """The test database as a postgresql:// URI (the fixtures give key=value conninfo)."""
    p = conninfo_to_dict(url)
    secret = f":{password}" if password else ""
    return f"postgresql://{p['user']}{secret}@{p['host']}:{p['port']}/{p['dbname']}"


def _variants(url: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """URLs whose connection really uses the dev password. A text check sees the first two only
    if it decodes them; the last needs the connection itself."""
    monkeypatch.setenv("PGPASSWORD", DEV_DATABASE_PASSWORD)  # for the PGPASSWORD variant
    return {
        "percent-encoded": _uri(url, DEV_DATABASE_PASSWORD.replace("-", "%2D")),
        "password-in-the-query": f"{_uri(url)}?password={DEV_DATABASE_PASSWORD}",
        "PGPASSWORD": _uri(url),
    }


def _no_secret(error: BaseException, url: str) -> None:
    text = str(error)
    assert DEV_DATABASE_PASSWORD not in text and url not in text and "%2D" not in text


@pytest.mark.parametrize("variant", ["percent-encoded", "password-in-the-query", "PGPASSWORD"])
def test_connect_refuses_the_dev_password_however_it_reached_the_connection(
    database_url: str, production: None, monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    url = _variants(database_url, monkeypatch)[variant]
    with pytest.raises(ConfigError, match="dev database password") as refused:
        connect(url)
    _no_secret(refused.value, url)
    assert connect(url, production=False).close() is None  # the same URL, not production: fine


def test_every_entry_point_that_connects_is_guarded_and_writes_nothing(
    blank_database_url: str, production: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    variants = _variants(blank_database_url, monkeypatch)
    # The CLIs read the process settings for their --url default, which production must also pass.
    monkeypatch.setenv("BIOSENTRY_DATABASE_URL", variants["PGPASSWORD"])
    monkeypatch.setenv("BIOSENTRY_INGEST_KEY", KEY)
    for url in variants.values():
        for run in (
            lambda u=url: seed.ensure(u),
            lambda u=url: seed.main(["--url", u]),
            lambda u=url: seed.main(["--url", u, "--reset"]),
            lambda u=url: migrate.main(["--url", u]),
        ):
            with pytest.raises(ConfigError, match="dev database password"):
                run()
    with psycopg.connect(blank_database_url) as conn:  # nothing was created
        tables = conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
        assert tables.fetchone() == (0,)


def test_the_passphrase_command_and_the_app_are_guarded_too(
    database_url: str, production: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _variants(database_url, monkeypatch)["PGPASSWORD"]  # no text for Settings to see
    monkeypatch.setenv("BIOSENTRY_DATABASE_URL", url)
    monkeypatch.setenv("BIOSENTRY_INGEST_KEY", KEY)
    monkeypatch.setattr(sys, "stdin", io.StringIO("a perfectly good passphrase\n"))
    with pytest.raises(ConfigError, match="dev database password"):
        passphrase_cli.main(["rn-manalo"])
    with connect(database_url, production=False) as conn:
        row = conn.execute("SELECT passphrase_hash FROM clinician WHERE id = 'rn-manalo'").fetchone()
    assert row is not None and row["passphrase_hash"] is not None  # untouched: still the dev one
    with pytest.raises(ConfigError, match="dev database password"):
        create_app(Settings(env="production", database_url=url, ingest_key=KEY))


def test_production_never_seeds_the_dev_passphrase_even_on_loopback(
    blank_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Let the connection pass the password guard, so only the seeding decision is under test.
    monkeypatch.setattr(postgres, "DEV_DATABASE_PASSWORD", "something-else")
    seed.ensure(blank_database_url, production=True)
    with connect(blank_database_url, production=False) as conn:
        hashes = conn.execute("SELECT passphrase_hash FROM clinician").fetchall()
    assert len(hashes) == 5 and {h["passphrase_hash"] for h in hashes} == {None}


def test_an_app_started_in_a_production_process_seeds_no_dev_passphrase_and_none_signs_in(
    blank_database_url: str, production: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(postgres, "DEV_DATABASE_PASSWORD", "something-else")
    from fastapi.testclient import TestClient

    client = TestClient(create_app(Settings(database_url=blank_database_url)))  # env says local
    body: dict[str, Any] = {"clinician_id": "rn-manalo", "passphrase": seed.DEV_PASSPHRASE}
    assert client.post("/session", json=body).status_code == 401


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, False), ("", False), ("local", False), (" LOCAL ", False), ("production", True),
     ("Production", True), ("prod", True), ("staging", True)],
)
def test_anything_but_local_counts_as_production_for_the_connection_guard(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool
) -> None:
    if value is None:
        monkeypatch.delenv("BIOSENTRY_ENV", raising=False)
    else:
        monkeypatch.setenv("BIOSENTRY_ENV", value)
    assert restricted() is expected
