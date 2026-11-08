"""Process settings. Read from the environment once, at start-up."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DATASET = _REPO_ROOT / "frontend" / "src" / "data" / "synthetic.json"
# The dev database from compose.yaml (loopback only, dev-only password).
DEFAULT_DATABASE_URL = "postgresql://biosentry:biosentry-dev-only@127.0.0.1:5432/biosentry"
# The dashboard (5173, 5174) and the ward sim (5180, 5181), on either spelling of loopback.
DEFAULT_CORS_ORIGINS = tuple(
    f"http://{host}:{port}"
    for host in ("localhost", "127.0.0.1")
    for port in (5173, 5174, 5180, 5181)
)


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = DEFAULT_DATABASE_URL
    dataset: Path = DEFAULT_DATASET
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    # Unset: the sim ingest routes refuse everything (fail closed).
    ingest_key: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        source = os.environ if env is None else env
        return cls(
            database_url=source.get("BIOSENTRY_DATABASE_URL") or DEFAULT_DATABASE_URL,
            ingest_key=source.get("BIOSENTRY_INGEST_KEY") or None,
        )
