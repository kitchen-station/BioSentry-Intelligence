"""Settings for the research agent, read once per command from the environment."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[2]
DATA = Path("~/Desktop/Projects/biosentry-data").expanduser()


class AgentError(Exception):
    """Every refusal in this package. The message is one line."""


@dataclass(frozen=True)
class Config:
    trials: Path
    home: Path
    models: Path
    sim: Path | None = None
    model_url: str | None = None
    model_name: str | None = None
    model_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        home = self.home.resolve()
        if home.is_relative_to(self.models.resolve()) or home.is_relative_to(REPO):
            raise AgentError("the agent home must not be inside the models folder or the repository")

    @property
    def model_on(self) -> bool:
        return bool(self.model_url and self.model_name and self.model_key)

    @classmethod
    def from_env(cls, env=None) -> "Config":
        env = os.environ if env is None else env
        path = lambda key, default: Path(env.get(key) or default).expanduser()  # noqa: E731
        url = env.get("BIOSENTRY_AGENT_MODEL_URL") or None
        if url and (not url.startswith("https://") or not urlparse(url).hostname):
            raise AgentError("BIOSENTRY_AGENT_MODEL_URL must be an https:// URL")
        sim = env.get("BIOSENTRY_AGENT_SIM")
        return cls(
            trials=path("BIOSENTRY_AGENT_TRIALS", DATA / "trials"),
            home=path("BIOSENTRY_AGENT_HOME", DATA / "agent"),
            models=path("BIOSENTRY_AGENT_MODELS", REPO / "models"),
            sim=Path(sim).expanduser() if sim else None,
            model_url=url,
            model_name=env.get("BIOSENTRY_AGENT_MODEL_NAME") or None,
            model_key=env.get("BIOSENTRY_AGENT_MODEL_KEY") or None,
        )
