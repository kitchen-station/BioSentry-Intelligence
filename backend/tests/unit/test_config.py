"""Settings: what the environment sets, and what production refuses to start with (the message
names the variable, never the URL or the key)."""

import pytest

from biosentry.core.config import (
    DEFAULT_CORS_ORIGINS,
    DEFAULT_DATABASE_URL,
    ConfigError,
    Settings,
)

URL = "postgresql://ward:Sentinel-DB-Password-81@db.internal.example:5432/biosentry?sslmode=require"
KEY = "Sentinel-Ingest-Key-0123456789abcdef"  # 36 characters
PRODUCTION = {"BIOSENTRY_ENV": "production", "BIOSENTRY_DATABASE_URL": URL, "BIOSENTRY_INGEST_KEY": KEY}


def test_local_is_the_default_and_keeps_the_dev_defaults() -> None:
    settings = Settings.from_env({})
    assert settings.env == "local"
    assert settings.database_url == DEFAULT_DATABASE_URL
    assert settings.ingest_key is None  # the sim routes fail closed
    assert settings.cors_origins == DEFAULT_CORS_ORIGINS
    assert "http://localhost:5173" in DEFAULT_CORS_ORIGINS


def test_local_accepts_the_dev_database_and_a_short_key() -> None:
    assert Settings.from_env({"BIOSENTRY_INGEST_KEY": "dev-local"}).ingest_key == "dev-local"


@pytest.mark.parametrize("given", ["", " ", ",", " , ,"])
def test_an_empty_cors_override_keeps_the_defaults(given: str) -> None:
    assert Settings.from_env({"BIOSENTRY_CORS_ORIGINS": given}).cors_origins == DEFAULT_CORS_ORIGINS


def test_cors_origins_can_be_overridden_with_a_comma_separated_list() -> None:
    given = " https://ward.tail1234.ts.net , http://localhost:5173,, "
    settings = Settings.from_env({"BIOSENTRY_CORS_ORIGINS": given})
    assert settings.cors_origins == ("https://ward.tail1234.ts.net", "http://localhost:5173")


def test_production_starts_with_a_real_database_and_a_long_key() -> None:
    settings = Settings.from_env(PRODUCTION)
    assert (settings.env, settings.database_url, settings.ingest_key) == ("production", URL, KEY)


def test_the_environment_name_is_trimmed_and_case_insensitive() -> None:
    assert Settings.from_env({**PRODUCTION, "BIOSENTRY_ENV": " Production "}).env == "production"
    with pytest.raises(ConfigError):  # and in production it is checked
        Settings.from_env({"BIOSENTRY_ENV": " Production "})


@pytest.mark.parametrize("name", ["prod", "staging", "dev", "1"])
def test_an_unknown_environment_is_refused_not_treated_as_local(name: str) -> None:
    with pytest.raises(ConfigError, match="BIOSENTRY_ENV must be local or production"):
        Settings.from_env({**PRODUCTION, "BIOSENTRY_ENV": name})


DEV_PASSWORD_URL = "postgresql://u:biosentry-dev-only@postgres:5432/biosentry"


@pytest.mark.parametrize(
    ("change", "complaint"),
    [
        ({"BIOSENTRY_DATABASE_URL": DEFAULT_DATABASE_URL}, "BIOSENTRY_DATABASE_URL"),
        ({"BIOSENTRY_DATABASE_URL": DEV_PASSWORD_URL}, "BIOSENTRY_DATABASE_URL"),  # another host
        ({"BIOSENTRY_DATABASE_URL": "host=db password=biosentry-dev-only"}, "BIOSENTRY_DATABASE_URL"),
        ({"BIOSENTRY_DATABASE_URL": ""}, "BIOSENTRY_DATABASE_URL"),  # unset: the dev default
        ({"BIOSENTRY_INGEST_KEY": ""}, "BIOSENTRY_INGEST_KEY"),
        ({"BIOSENTRY_INGEST_KEY": "x" * 31}, "BIOSENTRY_INGEST_KEY"),
        ({"BIOSENTRY_INGEST_KEY": " " * 40}, "BIOSENTRY_INGEST_KEY"),  # blank once stripped
        ({"BIOSENTRY_INGEST_KEY": "a" * 40}, "BIOSENTRY_INGEST_KEY"),  # one character, repeated
        ({"BIOSENTRY_INGEST_KEY": "ab" * 16}, "BIOSENTRY_INGEST_KEY"),  # 32 long, 2 distinct
        ({"BIOSENTRY_INGEST_KEY": "abcdefg" * 5}, "BIOSENTRY_INGEST_KEY"),  # 35 long, 7 distinct
        ({"BIOSENTRY_INGEST_KEY": "  " + "x" * 30}, "BIOSENTRY_INGEST_KEY"),  # 32 with padding
        ({"BIOSENTRY_DATABASE_URL": "postgresql://u:biosentry%2Ddev%2Donly@postgres/x"}, "BIOSENTRY_DATABASE_URL"),
        ({"BIOSENTRY_DATABASE_URL": "postgresql://u@postgres/x?password=biosentry-dev-only"}, "BIOSENTRY_DATABASE_URL"),
    ],
    ids=[
        "dev-default", "dev-password", "dev-password-keywords", "unset-url", "no-key", "short-key",
        "blank-key", "repeated-key", "two-distinct", "seven-distinct", "padded-short",
        "encoded-password", "password-query",
    ],
)
def test_production_refuses_to_start_and_never_prints_the_url_or_the_key(
    change: dict[str, str], complaint: str
) -> None:
    env = {**PRODUCTION, **change}
    with pytest.raises(ConfigError, match="refusing to start in production") as refused:
        Settings.from_env(env)
    message = str(refused.value)
    assert complaint in message
    for secret in (*env.values(), "Sentinel", "biosentry-dev-only"):
        if secret and secret != "production":
            assert secret not in message, secret


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://biosentry:Sentinel-DB-Password-81@postgres:5432/biosentry",  # the compose service
        "postgresql://u:Sentinel-DB-Password-81@127.0.0.1/x",  # the host is not what is checked
        "postgresql://u:Sentinel-DB-Password-81@localhost/x",
    ],
)
def test_production_does_not_judge_the_database_host(url: str) -> None:
    assert Settings.from_env({**PRODUCTION, "BIOSENTRY_DATABASE_URL": url}).database_url == url


def test_production_with_no_key_at_all_is_refused() -> None:
    env = {k: v for k, v in PRODUCTION.items() if k != "BIOSENTRY_INGEST_KEY"}
    with pytest.raises(ConfigError, match="BIOSENTRY_INGEST_KEY"):
        Settings.from_env(env)


def test_every_problem_is_named_at_once() -> None:
    with pytest.raises(ConfigError) as refused:
        Settings(env="production")  # the dev database and no key
    assert "BIOSENTRY_DATABASE_URL" in str(refused.value) and "BIOSENTRY_INGEST_KEY" in str(refused.value)


def test_a_key_of_32_characters_with_8_distinct_is_enough_and_is_stripped() -> None:
    key = "abcdefgh" * 4  # exactly 32 characters, exactly 8 distinct
    assert Settings.from_env({**PRODUCTION, "BIOSENTRY_INGEST_KEY": key}).ingest_key == key
    padded = Settings.from_env({**PRODUCTION, "BIOSENTRY_INGEST_KEY": f"  {key}\n"})
    assert padded.ingest_key == key  # the key the app compares against has no padding


def test_the_key_setup_makes_passes() -> None:
    hex_key = "0123456789abcdef" * 4  # what `openssl rand -hex 32` looks like
    assert Settings.from_env({**PRODUCTION, "BIOSENTRY_INGEST_KEY": hex_key}).ingest_key == hex_key


def test_a_blank_key_is_unset_in_local_too() -> None:
    assert Settings.from_env({"BIOSENTRY_INGEST_KEY": "   "}).ingest_key is None


def test_retention_is_seven_days_unless_the_environment_says_otherwise() -> None:
    assert Settings.from_env({}).retention_days == 7
    assert Settings.from_env({"BIOSENTRY_RETENTION_DAYS": ""}).retention_days == 7
    assert Settings.from_env({"BIOSENTRY_RETENTION_DAYS": " 30 "}).retention_days == 30


@pytest.mark.parametrize("given", ["0", "-1", "1.5", "a week", "36501", "9" * 5000])
def test_a_retention_that_is_not_a_whole_number_of_days_in_range_is_refused(given: str) -> None:
    with pytest.raises(ConfigError) as error:
        Settings.from_env({"BIOSENTRY_RETENTION_DAYS": given})
    # The one message, whatever was given: it names the variable and never the value.
    assert str(error.value) == "BIOSENTRY_RETENTION_DAYS must be a whole number from 1 to 36500"
    with pytest.raises(ConfigError, match="BIOSENTRY_RETENTION_DAYS"):
        Settings(retention_days=0)


def test_production_refuses_a_wildcard_cors_origin() -> None:
    for origins in ("*", "https://biosentry-dashboard.pages.dev,https://*.pages.dev"):
        with pytest.raises(ConfigError, match="exact origins"):
            Settings.from_env({**PRODUCTION, "BIOSENTRY_CORS_ORIGINS": origins})
    assert Settings.from_env({"BIOSENTRY_CORS_ORIGINS": "*"}).cors_origins == ("*",)  # local only


@pytest.mark.parametrize(
    ("given", "expected"),
    [(None, False), ("", False), ("  ", False), ("false", False), (" FALSE ", False), ("true", True), (" True ", True), ("TRUE", True)],
)
def test_the_open_ward_view_is_off_unless_it_is_set_to_true(given: str | None, expected: bool) -> None:
    env = {} if given is None else {"BIOSENTRY_OPEN_WARD_VIEW": given}
    assert Settings.from_env(env).open_ward_view is expected


@pytest.mark.parametrize("given", ["1", "yes", "on", "ture", "truee", "0", "no", "off", "tru e"])
def test_any_other_word_for_the_open_ward_view_stops_the_start(given: str) -> None:
    with pytest.raises(ConfigError) as refused:
        Settings.from_env({"BIOSENTRY_OPEN_WARD_VIEW": given})
    assert str(refused.value) == "BIOSENTRY_OPEN_WARD_VIEW must be true or false"


def test_production_starts_with_the_open_ward_view_on() -> None:
    settings = Settings.from_env({**PRODUCTION, "BIOSENTRY_OPEN_WARD_VIEW": "true"})
    assert (settings.env, settings.open_ward_view) == ("production", True)
