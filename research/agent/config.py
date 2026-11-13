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
        home, models = self.home.resolve(), self.models.resolve()
        if home.is_relative_to(models) or models.is_relative_to(home) or home.is_relative_to(REPO):
            raise AgentError("the agent home must not be inside the models folder or the repository, "
                             "and the models folder must not be inside the agent home")

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
        key = env.get("BIOSENTRY_AGENT_MODEL_KEY") or None
        if key and len(key) < 16:  # the checks that keep the key out of the ledger look for runs of 8 characters
            raise AgentError("BIOSENTRY_AGENT_MODEL_KEY is shorter than 16 characters")
        sim = env.get("BIOSENTRY_AGENT_SIM")
        return cls(
            trials=path("BIOSENTRY_AGENT_TRIALS", DATA / "trials"),
            home=path("BIOSENTRY_AGENT_HOME", DATA / "agent"),
            models=path("BIOSENTRY_AGENT_MODELS", REPO / "models"),
            sim=Path(sim).expanduser() if sim else None,
            model_url=url,
            model_name=env.get("BIOSENTRY_AGENT_MODEL_NAME") or None,
            model_key=key,
        )
