"""Process settings. Read from the environment once, at start-up.

    BIOSENTRY_ENV            local (default) or production
    BIOSENTRY_DATABASE_URL   the database; the compose dev one when unset
    BIOSENTRY_INGEST_KEY     the ward sim's key; unset: the sim routes refuse everything
    BIOSENTRY_CORS_ORIGINS   comma-separated origins the dashboard is served from; the local
                             dashboard and sim ports when unset
    BIOSENTRY_RETENTION_DAYS days of sim time a run keeps its vital bins and superseded observations
                             (7 when unset); a whole number from 1 to 36500
    BIOSENTRY_MODELS_DIR     the folder holding the model files and manifest.json; the repository's
                             `models/` when unset (a checkout, and /app/models in the image, which are
                             the same rule: the folder three levels above the package)

In production the process refuses to start on a database URL that holds the dev password (an early
check on the text) or an ingest key that is not 32 or more characters, at least 8 of them distinct,
once stripped. Whatever the URL says, `repositories.postgres.connect` also refuses a connection whose
actual password, as libpq resolved it (PGPASSWORD, a passfile, percent-encoding), is the dev one, at
every entry point that connects. The messages name the variable, never its value. (The host is not
checked: deployed, it is the compose service name `postgres`.)
"""

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DATASET = _REPO_ROOT / "frontend" / "src" / "data" / "synthetic.json"
DEFAULT_MODELS_DIR = _REPO_ROOT / "models"  # /app/models in the image, where the package sits at /app/backend/src
# The dev database from compose.yaml (loopback only, dev-only password).
DEV_DATABASE_PASSWORD = "biosentry-dev-only"
DEFAULT_DATABASE_URL = f"postgresql://biosentry:{DEV_DATABASE_PASSWORD}@127.0.0.1:5432/biosentry"
# The dashboard (5173, 5174) and the ward sim (5180, 5181), on either spelling of loopback.
DEFAULT_CORS_ORIGINS = tuple(
    f"http://{host}:{port}"
    for host in ("localhost", "127.0.0.1")
    for port in (5173, 5174, 5180, 5181)
)
LOOPBACK = ("127.0.0.1", "localhost", "::1")
ENVIRONMENTS = ("local", "production")
MIN_INGEST_KEY = 32
MIN_INGEST_KEY_DISTINCT = 8
MAX_RETENTION_DAYS = 36500  # a century: the cutoff stays a date Python and PostgreSQL hold


class ConfigError(ValueError):
    """The settings are refused. The message never contains a URL or a key."""


def restricted() -> bool:
    """Whether this process is anything but explicitly local (BIOSENTRY_ENV unset or `local`).
    Fails closed: a misspelt environment is treated as production by the checks that use this."""
    return (os.environ.get("BIOSENTRY_ENV") or "local").strip().lower() != "local"


def is_loopback(info: Any) -> bool:
    """Whether an ESTABLISHED connection is to this machine, from its `conn.info`: the server
    address libpq actually connected to (`hostaddr`, whatever the URL, PGHOSTADDR or a service
    file said) is a loopback address and `host` is a loopback name or address. Anything else, or
    anything that cannot be read (a unix socket has no hostaddr), is not loopback. Decides
    `--reset` and whether seeding may set the local dev passphrase; a URL alone never does."""
    try:
        return bool(info.host in LOOPBACK and ipaddress.ip_address(info.hostaddr).is_loopback)
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = DEFAULT_DATABASE_URL
    dataset: Path = DEFAULT_DATASET
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    # Unset: the sim ingest routes refuse everything (fail closed).
    ingest_key: str | None = None
    env: str = "local"
    retention_days: int = 7
    models_dir: Path = DEFAULT_MODELS_DIR

    def __post_init__(self) -> None:
        if not 1 <= self.retention_days <= MAX_RETENTION_DAYS:
            raise ConfigError(
                f"BIOSENTRY_RETENTION_DAYS must be a whole number from 1 to {MAX_RETENTION_DAYS}"
            )
        if self.env not in ENVIRONMENTS:
            raise ConfigError("BIOSENTRY_ENV must be local or production")
        if self.env != "production":
            return
        problems = []
        # An early check on the text; connect() checks the password the connection really used.
        if DEV_DATABASE_PASSWORD in unquote(self.database_url):  # covers the default URL itself
            problems.append("BIOSENTRY_DATABASE_URL must not be the dev default or hold the dev password")
        key = (self.ingest_key or "").strip()
        if len(key) < MIN_INGEST_KEY or len(set(key)) < MIN_INGEST_KEY_DISTINCT:
            problems.append(
                f"BIOSENTRY_INGEST_KEY must be set, at least {MIN_INGEST_KEY} characters of which"
                f" {MIN_INGEST_KEY_DISTINCT} differ"
            )
        if problems:
            raise ConfigError("refusing to start in production: " + "; ".join(problems))

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        source = os.environ if env is None else env
        origins = tuple(o.strip() for o in source.get("BIOSENTRY_CORS_ORIGINS", "").split(","))
        try:
            retention_days = int(source.get("BIOSENTRY_RETENTION_DAYS", "").strip() or 7)
        except ValueError:
            retention_days = 0  # refused below, by the one message
        return cls(
            database_url=source.get("BIOSENTRY_DATABASE_URL") or DEFAULT_DATABASE_URL,
            ingest_key=source.get("BIOSENTRY_INGEST_KEY", "").strip() or None,
            cors_origins=tuple(o for o in origins if o) or DEFAULT_CORS_ORIGINS,
            env=(source.get("BIOSENTRY_ENV") or "local").strip().lower(),
            retention_days=retention_days,
            models_dir=Path(source.get("BIOSENTRY_MODELS_DIR", "").strip() or DEFAULT_MODELS_DIR),
        )
