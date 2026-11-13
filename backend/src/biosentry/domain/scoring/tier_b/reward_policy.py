"""The reward-driven alerting policy: six numbers and plain arithmetic (INTEGRATION.md section 5; port of
research/reward_policy.py `Params`, `states` and research/reward_loop.py `load_policy`). Pure: the caller
passes numbers and booleans, nothing here reads a database, a clock or a file.

A flag from this policy adds a reason and changes no order (ruling 12). Research code: synthetic data only,
not validated.
"""

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from biosentry.domain.scoring.tier_b.baseline import VITALS

NAMES = ("heart_rate", "respiratory_rate", "spo2", "temperature")
BIN_MINUTES = 15
P_MAX = 8
THETA_MIN = 0.1
MAX_BYTES = 1 << 20  # the policy file's reader cap (section 2.2)
NAME = "tier_b/reward_policy"
_CONFIG = {"sigma0": (0.5, 0.25, 1.0), "shrink": (0.85, 0.7), "selection_trials": None, "form": ("sum", "max"),
           "fix_theta": (False, True), "frozen_vital": ("none", *NAMES)}


class PolicyError(ValueError):
    pass


def digest(body: object) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Params:
    weights: tuple[float, float, float, float]  # in NAMES order
    theta: float
    p: int

    def __post_init__(self) -> None:
        if len(self.weights) != 4 or any(not (w >= 0) or w == math.inf for w in self.weights):
            raise PolicyError("weights must be four finite non-negative numbers")
        if not (THETA_MIN <= self.theta < math.inf):
            raise PolicyError(f"theta must be finite and at least {THETA_MIN}")
        if type(self.p) is not int or not 1 <= self.p <= P_MAX:
            raise PolicyError(f"p must be an integer from 1 to {P_MAX}")


@dataclass(frozen=True, slots=True)
class Policy:
    params: Params
    temperature: bool
    content_sha256: str  # the file's own "sha256": the digest of its body


def _config_ok(cfg: object) -> bool:
    """The six search keys with research's allowed values (reward_loop.check_config); the form must be "sum",
    the only one the state machine below implements."""
    if not isinstance(cfg, dict) or set(cfg) != set(_CONFIG):
        return False
    for k, v in cfg.items():
        ok = _CONFIG[k]
        if k == "selection_trials":
            if type(v) is not int or not 1 <= v <= 64:
                return False
        elif type(v) is not type(ok[0]) or v not in ok:
            return False
    return cfg["form"] == "sum"


def parse_policy(obj: object, *, temperature: bool = True) -> Policy:
    """Port of load_policy: content sha256 of the body without "sha256", the temperature flag, the name, four
    weights and theta as numbers (no bool, no string), p, the config digest, no temperature weight when the
    policy has no temperature. Raises PolicyError."""
    if not isinstance(obj, dict) or not isinstance(obj.get("sha256"), str):
        raise PolicyError("not a policy file")
    if digest({k: v for k, v in obj.items() if k != "sha256"}) != obj["sha256"]:
        raise PolicyError("content does not match its sha256")
    if obj.get("temperature") is not temperature or obj.get("name") != (NAME if temperature else NAME + "/no_temperature"):
        raise PolicyError("temperature flag or name is not the expected one")
    try:
        d = obj["params"]
        nums = [d["weights"][v] for v in NAMES] + [d["theta"]]
        if set(d) != {"weights", "theta", "p"} or set(d["weights"]) != set(NAMES):
            raise PolicyError("params has unknown keys")
        if any(type(x) not in (int, float) or not math.isfinite(x) for x in nums):
            raise PolicyError("weights and theta must be finite numbers")
        params = Params(tuple(float(x) for x in nums[:4]), float(nums[4]), d["p"])  # type: ignore[arg-type]
        if not _config_ok(obj["config"]) or digest(obj["config"]) != obj["config_sha256"]:
            raise PolicyError("config is not allowed or does not match config_sha256")
    except (KeyError, TypeError) as e:
        raise PolicyError(f"bad params ({type(e).__name__})") from None
    if not temperature and params.weights[3] != 0.0:
        raise PolicyError("no-temperature policy with a temperature weight")
    return Policy(params, temperature, obj["sha256"])


def _number(v: object, what: str) -> float:
    if type(v) not in (int, float) or not math.isfinite(v):  # type: ignore[arg-type]
        raise ValueError(f"{what} must be a finite number")
    return float(v)  # type: ignore[arg-type]


def bin_z(signed_z: Mapping[str, float | None]) -> tuple[float, float, float, float]:
    """Section 5.2: z_v = max(0, bad_v * z), clipped at 0 in the bad direction (heart rate, respiratory rate and
    temperature rising, SpO2 falling), for the four vitals in NAMES order. `signed_z` is the baseline's signed z
    per vital. None (baseline forming or no value) gives 0.0, as research does for an EARLIER bin; the product
    does not compute the CURRENT bin while any baseline forms (ruling 10), which is the caller's gate."""
    if set(signed_z) != set(NAMES):
        raise ValueError(f"signed_z needs exactly the vitals {NAMES}")
    return tuple(  # type: ignore[return-value]
        0.0 if signed_z[v] is None else max(0.0, VITALS[v].bad * _number(signed_z[v], v)) for v in NAMES
    )


def score(params: Params, z: Sequence[float]) -> float:
    """Form "sum": the weighted sum of the four clipped z, in NAMES order."""
    w = params.weights
    if len(z) != 4:
        raise ValueError("z needs four values")
    z = [_number(x, "z") for x in z]
    return w[0] * z[0] + w[1] * z[1] + w[2] * z[2] + w[3] * z[3]


def alert_states(params: Params, bins: Sequence[tuple[int, float | None]]) -> list[bool]:
    """Section 5.2's state machine over one stay's stored bins in time order, as (minute, score). A score of
    None is a NOT RANKED bin. A gap between bins (minutes not 15 apart) or a NOT RANKED bin ends the run;
    the bin after a gap is still scored (its run is 1). A bin alerts when its run of consecutive scored bins
    with score >= theta is at least p. Minutes must be ints and strictly increasing, else ValueError."""
    out: list[bool] = []
    run, previous = 0, None
    for minute, s in bins:
        if type(minute) is not int or (previous is not None and minute <= previous):
            raise ValueError("minutes must be integers in strictly increasing order")
        if previous is not None and minute - previous != BIN_MINUTES:
            run = 0
        previous = minute
        if s is None:
            run = 0
            out.append(False)
            continue
        run = run + 1 if _number(s, "score") >= params.theta else 0
        out.append(run >= params.p)
    return out
