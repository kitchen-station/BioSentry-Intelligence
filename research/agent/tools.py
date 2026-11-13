"""The five tools the agent may call, and `call`, the only way to run one.

Nothing in an argument is a path or a command: datasets and rules come from fixed lists, seeds are
integers 1 to 10, and every file is found under the trial root or the agent home by code.
"""

import dataclasses
import hashlib
import json
import math
import os
import re
import resource
import signal
import subprocess
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from biosentry.domain.scoring.tier_a import News2Score

from research import baseline_policy, evaluate, events, reward_loop, reward_policy, sweep
from research.alerts import POLICIES, Policy
from research.evaluate import Pool
from research.rescore import check_parity, score_row
from research.trials import TrialError, _int_field, _json, load_trial, show

from .config import REPO, AgentError, Config


class ToolError(AgentError):
    pass


MAX_RESULT = 64 * 1024
CAPS = {"list_trials": 600, "evaluate_rules": 600, "sweep_baseline": 1800, "compare_runs": 600}
REWARD_TIMEOUT = 3600
LOG_TAIL = 4096
MAX_CHILD_FILE = 16 * 1024 * 1024  # any one file the reward loop writes, its log included
MAX_GENERATIONS = 64
MAX_GENERATION_BYTES = 256 * 1024
TIMED_OUT = -1

# --- schemas: plain data, so the model prompt can show them -------------------------------------

DATASETS = ["ward-v2", "ward-v1"]
_REWARD = {"run": {"pattern": "S-[0-9]{4}"}, "no_temperature": {"bool": True}}
RULE = {"variants": {
    "news2_chart_trigger": {},
    "news2_total_ge": {"threshold": {"int": [1, 20]}},
    "baseline_alone": {},
    "news2_or_baseline": {},
    "reward_policy": _REWARD,
    "news2_or_reward_policy": _REWARD,
}}
_DATASET = {"enum": DATASETS}
_SEEDS = {"list": {"int": [1, 10]}, "min": 1, "max": 10, "unique": True}
SCHEMAS = {
    "list_trials": {"args": {"dataset": _DATASET}, "optional": []},
    "evaluate_rules": {
        "args": {"dataset": _DATASET, "seeds": _SEEDS,
                 "rules": {"list": RULE, "min": 1, "max": 6, "unique": True},
                 "window": {"enum": ["early", "standard"]}},
        "optional": ["window"],
    },
    "run_reward_round": {
        "args": {"train_dataset": {"enum": ["ward-v2-train", "ward-v1-train"]},
                 "validate_dataset": _DATASET, "no_temperature": {"bool": True}},
        "optional": ["no_temperature"],
    },
    "sweep_baseline": {"args": {"dataset": _DATASET, "seeds": _SEEDS}, "optional": []},
    "compare_runs": {
        "args": {"seed": {"int": [1, 10]}, "days": {"int": [1, 30]},
                 "baseline_rule": RULE, "change_rule": RULE},
        "optional": [],
    },
}
DEFAULTS = {"window": "early", "no_temperature": False}


def _fields(fields: dict, obj: dict, where: str, skip=(), optional=()) -> None:
    extra = set(obj) - set(fields) - set(skip)
    if extra:
        raise ToolError(f"{where}: unknown key {show(sorted(extra, key=str)[0])!r}")
    for key, spec in fields.items():
        if key in obj:
            _check(spec, obj[key], f"{where}.{key}")
        elif key not in optional:
            raise ToolError(f"{where}: missing {key}")


def _check(spec: dict, v: object, where: str) -> None:
    def bad(why: str) -> ToolError:
        return ToolError(f"{where}: {why}")

    if "enum" in spec:
        if type(v) is not str or v not in spec["enum"]:
            raise bad(f"not one of {', '.join(spec['enum'])}: {show(v)!r}")
    elif "int" in spec:  # type() is int: a bool or a numeric string is refused
        lo, hi = spec["int"]
        if type(v) is not int or not lo <= v <= hi:
            raise bad(f"must be an integer from {lo} to {hi}")
    elif "bool" in spec:
        if type(v) is not bool:
            raise bad("must be true or false")
    elif "pattern" in spec:
        if type(v) is not str or not re.fullmatch(spec["pattern"], v):
            raise bad(f"must match {spec['pattern']}")
    elif "list" in spec:
        if type(v) is not list or not spec["min"] <= len(v) <= spec["max"]:
            raise bad(f"must be a list of {spec['min']} to {spec['max']} items")
        for i, x in enumerate(v):
            _check(spec["list"], x, f"{where}[{i}]")
        if spec.get("unique") and len({json.dumps(x, sort_keys=True) for x in v}) != len(v):
            raise bad("has repeats")
    else:
        if type(v) is not dict or type(v.get("rule")) is not str or v["rule"] not in spec["variants"]:
            raise bad("unknown rule")
        _fields(spec["variants"][v["rule"]], v, where, skip=("rule",))


def validate(name: object, args: object) -> dict:
    """The arguments with defaults filled in, or a ToolError."""
    if type(name) is not str or name not in SCHEMAS:
        raise ToolError(f"unknown tool: {show(name)!r}")
    if type(args) is not dict:
        raise ToolError(f"{name}: arguments must be an object")
    schema = SCHEMAS[name]
    _fields(schema["args"], args, name, optional=schema["optional"])
    return {**{k: DEFAULTS[k] for k in schema["optional"]}, **args}


# --- where trial data is read ----------------------------------------------------------------------

def _dir(cfg: Config, *parts: str) -> Path:
    root = cfg.trials.resolve()
    path = root.joinpath(*parts).resolve()
    if path == root or not path.is_relative_to(root):
        raise ToolError(f"{show(parts[-1])}: outside the trial root")
    return path


def _folder(cfg: Config, dataset: str, seed: int) -> Path:
    """A tuning seed's folder. The manifest is read first: a manifest naming another seed is
    refused before cohort.csv or episodes.csv is opened."""
    name = f"seed-{seed:02d}"
    if seed not in evaluate.TUNING:
        raise ToolError(f"{name}: not a tuning seed")
    folder = _dir(cfg, dataset, name)
    if folder.name != name or not folder.is_dir():
        raise ToolError(f"{dataset}/{name}: missing")
    found = _int_field(_json(folder, "manifest.json"), "seed", "manifest.json", 0, 2**31 - 1)
    if found != seed:
        raise ToolError(f"{dataset}/{name}: manifest says seed {found}; refused")
    return folder


def _div(n: float, d: float, places: int) -> float | None:
    return round(n / d, places) if d else None


def _get(obj: object, *keys: str) -> object:
    for k in keys:
        obj = obj.get(k) if isinstance(obj, dict) else None
    return obj


def _num(x: object) -> float | int | None:
    return x if type(x) in (int, float) and math.isfinite(x) else None


def _json_file(path: Path, cap: int) -> dict:
    try:
        if path.is_symlink():
            raise OSError
        with open(path, "rb") as f:
            data = f.read(cap + 1)
        obj = json.loads(data)
    except (OSError, ValueError, RecursionError):
        raise ToolError(f"{show(path.name)}: cannot be read as JSON") from None
    if len(data) > cap or not isinstance(obj, dict):
        raise ToolError(f"{show(path.name)}: larger than {cap} bytes or not an object")
    return obj


# --- 1. list_trials -------------------------------------------------------------------------------

@dataclass
class TrialList:
    dataset: str
    trials: list[dict]


def list_trials(cfg: Config, dataset: str) -> TrialList:
    base = _dir(cfg, dataset)
    if not base.is_dir():
        raise ToolError(f"{dataset}: missing")
    out = []
    for p in sorted(base.iterdir()):
        if not re.fullmatch(r"seed-[0-9]{2}", p.name) or not p.is_dir():
            continue
        folder = _dir(cfg, dataset, p.name)
        manifest = _json(folder, "manifest.json")
        seed = _int_field(manifest, "seed", "manifest.json", 0, 2**31 - 1)
        if seed != int(p.name[5:]):
            raise ToolError(f"{dataset}/{p.name}: manifest says seed {seed}; refused")
        split = ("tuning" if seed in evaluate.TUNING else "held out" if seed in evaluate.HELD_OUT
                 else "training" if seed >= 31 else "other")
        out.append({"seed": seed, "days": _int_field(manifest, "days", "manifest.json", 1, 60), "split": split})
    return TrialList(dataset, out)


# --- 2. evaluate_rules ----------------------------------------------------------------------------

def _total_ge(n: int) -> Policy:
    def alerting(row) -> bool:
        score = score_row(row)
        return isinstance(score, News2Score) and score.total >= n

    return Policy(f"news2_total_ge_{n}", alerting, POLICIES[0].abstains)


def _policies(cfg: Config, rules: list[dict]) -> list[Policy]:
    params, _ = evaluate._baseline_params()  # read only
    out = []
    for r in rules:
        kind = r["rule"]
        if kind == "news2_chart_trigger":
            out.append(POLICIES[0])
        elif kind == "news2_total_ge":
            out.append(_total_ge(r["threshold"]))
        elif kind in ("baseline_alone", "news2_or_baseline"):
            alone, either = baseline_policy.baseline_policies(params.k, params.p)
            out.append(alone if kind == "baseline_alone" else either)
        else:
            nt = r["no_temperature"]
            home = cfg.home.resolve()
            path = (home / "runs" / r["run"] / "reward_policy" / (reward_loop.NO_TEMP_DIR if nt else "")).resolve()
            if not path.is_relative_to(home):
                raise ToolError(f"{r['run']}: outside the agent home")
            name = f"reward_{r['run']}{'_no_temp' if nt else ''}"
            alone, either = reward_policy.policies(name, reward_loop.read_policy(path / "policy.json", not nt))
            out.append(alone if kind == "reward_policy" else either)
    return out


def _nurses(manifest: dict) -> int | None:
    n = _get(manifest, "scenario", "staffing", "staff_nurses")
    return n if type(n) is int and n > 0 else None


@dataclass
class Evaluation:
    trials: int
    seeds: list[int]
    window: str
    episodes_evaluated: int
    episodes_not_cut_short: int
    control_windows: int
    patient_days: float
    staff_nurses: int | None
    rules: list[dict]


def evaluate_rules(cfg: Config, dataset: str, seeds: list[int], rules: list[dict], window: str = "early") -> Evaluation:
    policies = _policies(cfg, rules)
    pool, days, nurses = Pool(), 0, set()
    for seed in seeds:
        folder = _folder(cfg, dataset, seed)
        trial = load_trial(folder)
        if trial.seed != seed:  # the manifest changed between the check and the load
            raise ToolError(f"{dataset}/{folder.name}: seed changed while reading")
        check_parity(trial)
        evaluate.score_trial(trial, tuple(policies), pool, 0 if window == "early" else events.TAIL_MINUTES)
        baseline_policy.clear_cache()
        days += trial.days
        nurses.add(_nurses(_json(folder, "manifest.json")))
    staff = nurses.pop() if len(nurses) == 1 else None
    pd = pool.patient_days
    out = []
    for p in policies:
        t = pool.tallies[p.name]
        out.append({
            "name": p.name,
            "alerts": t.alerts,
            "alerts_per_patient_12h": _div(t.alerts, pd * 2, 3),
            "alerts_per_100_patient_days": _div(100 * t.alerts, pd, 1),
            "alerts_per_nurse_per_shift": _div(t.alerts, days * 2 * staff, 2) if staff else None,
            "time_in_alert": _div(t.on_bins, t.bins, 3),
            "caught": [t.uncut_any, pool.uncut],
            "caught_share": _div(t.uncut_any, pool.uncut, 3),
            "chance": [t.ctrl_any, pool.controls],
            "chance_share": _div(t.ctrl_any, pool.controls, 3),
            "excess_over_chance": round(float(sweep.excess(t.uncut_any, pool.uncut, t.ctrl_any, pool.controls)), 3),
        })
    for r in out[1:]:
        r["vs_first"] = {
            "extra_alerts_per_100_patient_days": _div(100 * (r["alerts"] - out[0]["alerts"]), pd, 1),
            "extra_caught": r["caught"][0] - out[0]["caught"][0],
        }
    return Evaluation(len(seeds), list(seeds), window, pool.evaluated, pool.uncut, pool.controls,
                      round(pd, 1), staff, out)


# --- 3. run_reward_round --------------------------------------------------------------------------

def _limit_files() -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_CHILD_FILE, MAX_CHILD_FILE))


def _run(argv: list[str], log_path: Path, timeout: int) -> int:
    # The model key must not reach the child.
    env = {k: v for k, v in os.environ.items() if not k.startswith("BIOSENTRY_AGENT_")}
    with open(log_path, "wb") as log:
        try:
            return subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                  timeout=timeout, cwd=REPO, env=env, shell=False,
                                  preexec_fn=_limit_files).returncode
        except subprocess.TimeoutExpired:
            return TIMED_OUT


def _log_tail(path: Path) -> str:
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - LOG_TAIL))
        text = f.read().decode("utf-8", "replace")
    return "".join(c if c.isprintable() or c == "\n" else "?" for c in text)


def _generations(folder: Path) -> list[dict]:
    files = sorted((folder / "generations").glob("gen-[0-9][0-9].json"))
    if len(files) > MAX_GENERATIONS:
        raise ToolError(f"more than {MAX_GENERATIONS} generation files")
    out = []
    for f in files:
        g = _json_file(f, MAX_GENERATION_BYTES)
        if type(g.get("generation")) is not int:
            raise ToolError(f"{f.name}: generation must be an integer")
        accepted = g.get("accepted")
        out.append({  # only these typed fields; anything else in the file is ignored
            "generation": g["generation"],
            "accepted": accepted if type(accepted) is bool else None,
            "train_reward_before": _num(_get(g, "current_before", "train_reward")),
            "train_reward_best": _num(_get(g, "best_candidate", "train_reward")),
            "validation_reward": _num(_get(g, "validation", "reward")),
        })
    return out


@dataclass
class RewardRound:
    exit_code: int
    no_temperature: bool
    generations: list[dict]
    chosen_generation: int | None
    params: dict | None
    validation_reward: float | None
    policy_sha256: str | None
    log_tail: str


def run_reward_round(cfg: Config, run_dir: Path, train_dataset: str, validate_dataset: str,
                     no_temperature: bool = False) -> RewardRound:
    roots = [_dir(cfg, train_dataset), _dir(cfg, validate_dataset)]
    for root, dataset in zip(roots, (train_dataset, validate_dataset)):
        for p in sorted(root.iterdir()) if root.is_dir() else []:
            if p.name.startswith("seed-"):
                _dir(cfg, dataset, p.name)  # the loop opens these itself: none may leave the trial root
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / "reward_policy"
    argv = [sys.executable, "-m", "research.reward_loop", "--train", str(roots[0]),
            "--validate", str(roots[1]), "--out", str(out)]
    if no_temperature:
        argv.append("--no-temperature")
    log = run_dir / "loop.log"
    code = _run(argv, log, REWARD_TIMEOUT)
    tail = _log_tail(log)
    if code != 0:
        return RewardRound(code, no_temperature, [], None, None, None, None, tail)
    folder = out / reward_loop.NO_TEMP_DIR if no_temperature else out
    params = reward_loop.read_policy(folder / "policy.json", not no_temperature)  # checks its sha256
    raw = (folder / "policy.json").read_bytes()
    try:
        body = json.loads(raw)
    except ValueError:  # read_policy just parsed it, so the file changed in between
        raise ToolError("policy.json: changed while reading") from None
    chosen = body.get("chosen_generation")
    return RewardRound(code, no_temperature, _generations(folder),
                       chosen if type(chosen) is int else None, params.as_dict(),
                       _num(body.get("validation_reward")), hashlib.sha256(raw).hexdigest(), tail)


# --- 4. sweep_baseline ----------------------------------------------------------------------------

@dataclass
class Sweep:
    chosen: dict | None
    chart: dict
    row: dict | None
    settings_tried: int
    params_sha256: str | None


def sweep_baseline(cfg: Config, run_dir: Path, dataset: str, seeds: list[int]) -> Sweep:
    folders = [str(_folder(cfg, dataset, s)) for s in seeds]
    rows, best, ref = sweep.sweep(folders)
    if best is None:
        return Sweep(None, ref, None, len(rows), None)
    body = {"k": best.k, "p": best.p, "chosen_on": f"{dataset} seeds {','.join(map(str, sorted(seeds)))}",
            "rule": sweep.RULE, "grid": rows, "tuning": ref, "synthetic": True, "validated": False}
    data = (json.dumps(body, indent=1) + "\n").encode()
    run_dir.mkdir(parents=True, exist_ok=True)
    tmp = run_dir / "params.json.tmp"
    tmp.write_bytes(data)
    os.replace(tmp, run_dir / "params.json")
    row = next(r for r in rows if r["k"] == best.k and r["p"] == best.p)
    return Sweep({"k": best.k, "p": best.p}, ref, row, len(rows), hashlib.sha256(data).hexdigest())


# shortcut: the in-process cap below is a signal, so called code that catches every exception could
# outlive it (none in research/ does today), upgrade by running these tools in a child process
# --- 5. compare_runs ------------------------------------------------------------------------------

@dataclass
class Comparison:
    available: bool
    reason: str
    seed: int
    days: int


def compare_runs(cfg: Config, seed: int, days: int, baseline_rule: dict, change_rule: dict) -> Comparison:
    """Never runs a command: a guessed command line is worse than saying it is not available."""
    if cfg.sim is None:
        why = "no simulator path is configured (BIOSENTRY_AGENT_SIM)"
    else:
        try:
            scripts = _get(_json_file(Path(cfg.sim) / "package.json", 64 * 1024), "scripts")
        except ToolError:
            scripts = None
        if isinstance(scripts, dict) and "compare" in scripts:
            why = "a compare script was found, but this agent has no adapter checked against its command line"
        else:
            why = "the simulator path has no package.json with a compare script"
    return Comparison(False, why, seed, days)


TOOLS = {f.__name__: f for f in (list_trials, evaluate_rules, run_reward_round, sweep_baseline, compare_runs)}
WRITES = ("run_reward_round", "sweep_baseline")  # these get a run folder inside the agent home


@contextmanager
def _cap(seconds: float | None):
    if seconds is None:
        yield
        return
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "SIGALRM"):
        raise ToolError("no time cap is possible here, so the tool will not run")

    def late(*_):
        raise ToolError(f"took longer than {seconds} s")

    old = signal.signal(signal.SIGALRM, late)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def call(cfg: Config, name: str, args: dict, run_dir: Path | None = None) -> tuple[dict, str]:
    """Validate, run under the time cap, size-check. Returns (result, sha256 of its canonical JSON)."""
    args = validate(name, args)
    extra = {}
    if name in WRITES:
        runs = (cfg.home / "runs").resolve()
        if run_dir is None or Path(run_dir).resolve().parent != runs or not re.fullmatch(r"S-[0-9]{4}", Path(run_dir).name):
            raise ToolError(f"{name}: the run folder must be runs/S-NNNN inside the agent home")
        extra["run_dir"] = Path(run_dir)
    try:
        with _cap(CAPS.get(name)):
            result = TOOLS[name](cfg, **extra, **args)
    except TrialError as e:
        raise ToolError(str(e)) from None
    except OSError:
        raise ToolError(f"{name}: a file could not be read or written") from None
    body = dataclasses.asdict(result)
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    if len(canonical) > MAX_RESULT:
        raise ToolError(f"{name}: result is larger than 64 KiB")
    return body, hashlib.sha256(canonical).hexdigest()
