"""What to try next: a fixed list of experiments, a rule planner, and an optional model planner.

The model only picks one experiment from the open ones. Its answer is data: it is used only when it
parses, passes `tools.validate`, and equals an open experiment exactly.
"""

import http.client
import json
import math
import re
import urllib.request
from dataclasses import dataclass, replace
from urllib.parse import urlparse

from . import ledger, tools
from .config import Config
from .tools import ToolError

E1, E2, E3, E4, E5 = ("reward_round", "check_policy", "reward_round_no_temperature",
                      "check_policy_no_temperature", "sweep_baseline")
EXPERIMENTS = (E1, E2, E3, E4, E5)
RULES = {  # rule name, and the words printed with it
    E1: ("R1", "no reward round with temperature in the ledger yet"),
    E2: ("R2", "the last reward round chose a policy that has not been checked on tuning seeds 1 to 7"),
    E3: ("R3", "no reward round without temperature in the ledger yet"),
    E4: ("R4", "the last reward round without temperature chose a policy that has not been checked on tuning seeds 1 to 7"),
    E5: ("R5", "no baseline sweep in the ledger yet"),
}
NOTHING_LEFT = "R6: nothing left on the list; waiting for a person or new trials"
TIMEOUT = 20
MAX_RESPONSE = 64 * 1024
_RUN = re.compile(r"S-[0-9]{4}")


@dataclass(frozen=True)
class Plan:
    experiment: str
    tool: str
    args: dict
    rule: str
    reason: str
    planner: str = "rule"
    host: str | None = None  # the model planner's host
    fallback: str | None = None  # why a model answer was not used


def _args(exp: str, run: str | None) -> tuple[str, dict]:
    nt = exp.endswith("no_temperature")
    if exp.startswith("reward_round"):
        return "run_reward_round", {"train_dataset": "ward-v2-train", "validate_dataset": "ward-v2", "no_temperature": nt}
    if exp.startswith("check_policy"):
        rules = [{"rule": "news2_chart_trigger"}, {"rule": "news2_or_reward_policy", "run": run, "no_temperature": nt}]
        return "evaluate_rules", {"dataset": "ward-v2", "seeds": list(range(1, 8)), "window": "early", "rules": rules}
    return "sweep_baseline", {"dataset": "ward-v2", "seeds": list(range(1, 11))}


def _plan(exp: str, run: str | None = None) -> Plan:
    tool, args = _args(exp, run)
    return Plan(exp, tool, args, *RULES[exp])


# --- the state, built from typed fields only ----------------------------------------------------------

def _num(x: object) -> float | int | None:
    return x if type(x) in (int, float) and math.isfinite(x) else None


def _get(obj: object, *keys: object) -> object:
    for k in keys:
        try:
            obj = obj[k]
        except (KeyError, IndexError, TypeError):
            return None
    return obj


def _run_id(x: object) -> str | None:
    return x if type(x) is str and _RUN.fullmatch(x) else None


def state_from(records: list[dict]) -> dict:
    """For each finished round: its id, experiment name, whether the tool worked, and numbers.
    Never free text from a file, a person or the model."""
    rounds = []
    for r in records:
        exp = _get(r, "plan", "experiment")
        if r["type"] != "step" or r.get("kind") != "round" or exp not in EXPERIMENTS or not _run_id(r.get("id")):
            continue
        call = _get(r, "calls", 0)
        ok = _get(call, "ok") is True
        res = _get(call, "result") if ok else None
        s = {"id": r["id"], "experiment": exp, "ok": ok}
        if exp in (E1, E3):
            g = _get(res, "chosen_generation")
            s["chosen_generation"] = g if type(g) is int else None
            s["validation_reward"] = _num(_get(res, "validation_reward"))
        elif exp in (E2, E4):
            s["checked"] = _run_id(_get(r, "plan", "args", "rules", 1, "run"))
            for name, i in (("chart", 0), ("or", 1)):
                s[f"{name}_alerts"] = _num(_get(res, "rules", i, "alerts"))
                s[f"{name}_excess_over_chance"] = _num(_get(res, "rules", i, "excess_over_chance"))
        else:
            s["chosen_k"] = _num(_get(res, "chosen", "k"))
            s["chosen_p"] = _num(_get(res, "chosen", "p"))
        rounds.append(s)
    return {"rounds": rounds}


def open_experiments(state: dict) -> list[Plan]:
    """Every experiment from the list that is legal now, in list order."""
    rounds = state["rounds"]
    by = lambda exp: [r for r in rounds if r["experiment"] == exp]  # noqa: E731
    out = []
    for first, check in ((E1, E2), (E3, E4)):
        done = by(first)
        if not done:
            out.append(_plan(first))
        elif done[-1].get("chosen_generation") is not None and done[-1]["id"] not in {
                r["checked"] for r in by(check) if r["ok"]}:  # a check that failed is tried again
            out.append(_plan(check, done[-1]["id"]))
    if not by(E5):
        out.append(_plan(E5))
    return out


class RulePlanner:
    def next(self, state: dict) -> Plan | None:
        # list order is the rule order: E1/E2, then E3/E4, then E5
        return next(iter(open_experiments(state)), None)


# --- the model planner -----------------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # the 3xx becomes an HTTPError


def post(url: str, headers: dict, body: bytes, timeout: float) -> bytes:
    """One HTTPS POST. No redirects, and the answer is capped at 64 KiB."""
    if not url.startswith("https://"):
        raise ValueError("https only")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout) as response:
        data = response.read(MAX_RESPONSE + 1)
    if len(data) > MAX_RESPONSE:
        raise ValueError("answer too large")
    return data


INSTRUCTIONS = (
    "You pick the next experiment for a research agent that tests alerting rules on simulated hospital "
    "patients. The user message is JSON: the earlier rounds and the open experiments. Answer with one JSON "
    'object and nothing else: {"tool": <tool name>, "args": <arguments>, "reason": <one short sentence>}. '
    "Copy the tool and args of one open experiment exactly. Anything else is ignored. The tool schemas follow.\n"
)


class _Reject(Exception):
    def __init__(self, code: str, shown: str = ""):
        super().__init__(code)
        self.code, self.shown = code, shown


def _pick(raw: bytes, opens: list[Plan]) -> Plan:
    try:
        content = json.loads(raw)["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError, RecursionError):
        raise _Reject("bad_envelope") from None
    if type(content) is not str:
        raise _Reject("bad_envelope")
    try:
        answer = json.loads(content)
    except (ValueError, TypeError, RecursionError):
        raise _Reject("not_json", content) from None
    if not isinstance(answer, dict) or set(answer) != {"tool", "args", "reason"} or type(answer["reason"]) is not str:
        raise _Reject("wrong_keys", content)
    try:
        args = tools.validate(answer["tool"], answer["args"])
    except ToolError as e:
        raise _Reject("refused_args", str(e)) from None
    for p in opens:
        if (p.tool, p.args) == (answer["tool"], args):
            return replace(p, rule="model", reason=ledger.clean(answer["reason"], 200), planner="model")
    raise _Reject("not_open", content)


class ModelPlanner:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def next(self, state: dict) -> Plan | None:
        cfg = self.cfg
        opens = open_experiments(state)
        if not opens:
            return None
        host = urlparse(cfg.model_url).hostname or ""
        digest = {"rounds": state["rounds"],
                  "open": [{"experiment": p.experiment, "tool": p.tool, "args": p.args} for p in opens]}
        body = json.dumps({
            "model": cfg.model_name, "temperature": 0, "max_tokens": 300, "stream": False,
            "messages": [{"role": "system", "content": INSTRUCTIONS + json.dumps(tools.SCHEMAS)},
                         {"role": "user", "content": json.dumps(digest)}],
        }).encode()
        headers = {"Authorization": f"Bearer {cfg.model_key}", "Content-Type": "application/json"}
        try:
            try:
                raw = post(cfg.model_url, headers, body, TIMEOUT)
            except (OSError, ValueError, http.client.HTTPException):  # a timeout is an OSError
                raise _Reject("http_error") from None
            if cfg.model_key.encode() in raw:  # never parsed, so no part of it can be stored
                raise _Reject("echoed_key")
            return replace(_pick(raw, opens), host=host)
        except _Reject as e:
            ledger.add_fallback(cfg, e.code, host, e.shown.replace(cfg.model_key, "***"))
            return replace(opens[0], fallback=e.code)


def choose_planner(cfg: Config):
    return ModelPlanner(cfg) if cfg.model_on else RulePlanner()
