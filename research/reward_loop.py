"""Improve the alerting policy by reward over fresh simulator trials (slice M2; replaces B6's loop).

    python -m research.reward_loop --data <ward-v2-ext> --out <dir> --round R [--change key=value --why "text"] [--jobs N]
    python -m research.reward_loop --data <ward-v2-ext> --out <dir> --no-temperature [--jobs N]
    python -m research.reward_loop --out <dir> --stop "reason"
    python -m research.reward_loop --evaluate <ward-v2> --out <dir> [--held-out-set 1|2] [--json <held-out.json>]
    python -m research.reward_loop --run-log <file> --run-id <id> --commit <sha> --out <dir>

The rules are in research/README.md. A (1+30) evolution strategy with a reward: not deep or
per-step reinforcement learning. Exit 0 done, 2 refused (one line), 3 seeds are missing (the
range is on stderr and nothing was written for it). `--run-log` only reads the committed records (no trial is
opened) and writes one training run log. Standard library only.
"""

import argparse
import hashlib
import json
import math
import multiprocessing
import os
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import evaluate
from .alerts import POLICIES
from .baseline_policy import NO_TEMP, baseline_policies
from .bootstrap import SplitMix64
from .reward_policy import _MEMO as _POLICY_MEMO
from .reward_policy import FORMS, LAMBDA, NAMES, P_MAX, THETA_MIN, Params, Prepared, generation_zero, policies, prepare, score
from .rescore import check_parity
from .trials import Trial, TrialError, _int_field, _json, load_trial
from . import reward_loop_b6
from .reward_loop_b6 import policy_file  # noqa: F401  (legacy: research/agent/ still uses it)

HELD_OUT = evaluate.HELD_OUT
# Seeds 1001-1400 are held-out set 2 in ANY folder (same seed, same generator): no loader here may open them.
HELD_OUT_2 = evaluate.HELD_OUT_2
SEED = 20261112
NO_TEMP_DIR = "no_temperature"
LEDGER = "experiments.jsonl"
MAX_FILE_BYTES = 1 << 20
SCHEMA = 2
# One-sided 5% t quantiles by n trials (df = n - 1); 12 trials is the contract's confirmation batch.
T_CRIT = {2: 6.314, 3: 2.920, 4: 2.353, 5: 2.132, 6: 2.015, 7: 1.943, 8: 1.895, 9: 1.860, 10: 1.833,
          11: 1.812, 12: 1.796}
KEEP_SE = 2.0  # kept rule: validation reward must beat the incumbent by 2 standard errors

DEFAULT_CONFIG = {"sigma0": 0.5, "shrink": 0.85, "selection_trials": 8, "form": "sum", "fix_theta": False,
                  "frozen_vital": "none"}
MENU = {"sigma0": (0.5, 0.25, 1.0), "shrink": (0.85, 0.7), "selection_trials": (8, 16), "form": FORMS,
        "fix_theta": (False, True), "frozen_vital": ("none", *NAMES)}
SIGMA_FLOOR = {"w": 0.1, "theta": 0.1, "p": 0.25}


@dataclass(frozen=True)
class Plan:
    """Sizes and seed ranges. The defaults are the contract's; tests pass small numbers."""

    val_seeds: range = range(341, 401)
    first_seed: int = 401
    last_seed: int = 1000  # seeds 1001-1400 are held-out set 2
    confirm: int = 12
    candidates: int = 30
    max_generations: int = 40
    patience: int = 3
    max_rounds: int = 4
    base: dict = field(default_factory=lambda: dict(DEFAULT_CONFIG))

    @property
    def allowed(self) -> tuple[range, range]:
        return self.val_seeds, range(self.first_seed, self.last_seed + 1)


class NeedSeeds(Exception):
    def __init__(self, a: int, b: int) -> None:
        super().__init__(f"needs seeds {a} to {b}")


def refuse(msg: str) -> TrialError:
    return TrialError(msg)


def digest(body) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def sha(data: bytes | str) -> str:
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def _dump(obj: dict) -> bytes:
    return (json.dumps(obj, indent=1, sort_keys=True) + "\n").encode()


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _read_file(path: Path) -> bytes:
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise refuse(f"{path.name}: cannot be read") from None
    if len(data) > MAX_FILE_BYTES:
        raise refuse(f"{path.name}: larger than {MAX_FILE_BYTES} bytes")
    return data


def _text(s: str, what: str) -> str:
    if not s.strip() or len(s) > 600 or not s.isprintable():
        raise refuse(f"{what}: needs 1 to 600 printable characters, no control characters")
    return s


# --- configuration ----------------------------------------------------------------------------

def check_config(cfg) -> dict:
    """The six search keys. selection_trials may be any 1 to 64 here (tests use small numbers);
    `--change` accepts only the menu's values."""
    if not isinstance(cfg, dict) or set(cfg) != set(MENU):
        raise refuse("config: not the six search keys")
    for k, v in cfg.items():
        if type(v) is not type(MENU[k][0]) or not (1 <= v <= 64 if k == "selection_trials" else v in MENU[k]):
            raise refuse(f"config: {k} = {str(v)[:20]!r} is not allowed")
    return cfg


def parse_change(text: str, incumbent: dict) -> tuple[dict, dict]:
    """The incumbent's configuration with exactly one key changed, and the ledger's `change`."""
    key, eq, raw = text.partition("=")
    if not eq or key not in MENU:
        raise refuse(f"--change: key=value with a key from {', '.join(MENU)}")
    try:
        value = json.loads(raw)
    except ValueError:
        value = raw
    if type(value) is int and key in ("sigma0", "shrink"):
        value = float(value)
    if type(value) is not type(MENU[key][0]) or value not in MENU[key]:
        raise refuse(f"--change: {key} takes one of {', '.join(map(str, MENU[key]))}")
    if value == incumbent[key]:
        raise refuse(f"--change: {key} already has that value; a round changes exactly one key")
    return check_config({**incumbent, key: value}), {"key": key, "from": incumbent[key], "to": value}


def frozen_set(cfg: dict, temperature: bool) -> frozenset:
    return frozenset(({cfg["frozen_vital"]} - {"none"}) | (set() if temperature else {"temperature"}))


def start_params(p: Params, frozen: frozenset) -> Params:
    return Params(tuple(0.0 if n in frozen else w for n, w in zip(NAMES, p.weights)), p.theta, p.p)


def sigma_start(cfg: dict) -> dict:
    return {"w": cfg["sigma0"], "theta": cfg["sigma0"], "p": 2 * cfg["sigma0"]}


# --- the split, enforced where data is read ---------------------------------------------------

def _manifest(root, seed: int, allowed: tuple) -> tuple[Path, dict]:
    """The manifest is the first file opened. A held-out seed, one outside `allowed` (ext seeds
    61-340 among them) or a manifest naming another seed is refused before anything else is read."""
    if seed in HELD_OUT or seed in HELD_OUT_2 or not any(seed in r for r in allowed):
        raise TrialError(f"seed {seed}: not allowed here")
    folder = Path(root) / f"seed-{seed:02d}"
    if not folder.is_dir():
        raise TrialError(f"seed-{seed:02d}: missing")
    manifest = _json(folder, "manifest.json")
    found = _int_field(manifest, "seed", "manifest.json", 0, 2**31 - 1)
    if found != seed:
        raise TrialError(f"seed-{seed:02d}: manifest says seed {found}; refused")
    return folder, manifest


def _shape(manifest: dict) -> tuple:
    return manifest.get("days"), manifest.get("speed"), manifest.get("scenario_sha256")


def manifest_shape(root, seed: int, allowed: tuple) -> tuple:
    return _shape(_manifest(root, seed, allowed)[1])


def load_seed(root, seed: int, allowed: tuple, shape: tuple | None = None) -> Trial:
    folder, manifest = _manifest(root, seed, allowed)
    if shape is not None and _shape(manifest) != shape:
        raise TrialError(f"seed-{seed:02d}: days, speed or scenario hash differ from the validation trials'; refused")
    trial = load_trial(folder)
    if trial.seed != seed:
        raise TrialError(f"seed-{seed:02d}: seed changed while reading")
    check_parity(trial)
    return trial


# --- parallel work: results come back in input order, so the bytes do not depend on --jobs -----

_CTX = None


class Runner:
    """shortcut: fork start method only (Linux, the gate's container); without it, serial."""

    def __init__(self, jobs: int = 1) -> None:
        self.jobs = jobs

    def map(self, fn, items, ctx):
        global _CTX
        items = list(items)
        _CTX = ctx
        try:
            if self.jobs <= 1 or len(items) <= 1 or "fork" not in multiprocessing.get_all_start_methods():
                return [fn(i) for i in items]
            with multiprocessing.get_context("fork").Pool(min(self.jobs, len(items))) as pool:
                return pool.map(fn, items)
        finally:
            _CTX = None


def _cell(i: int):
    params, trials, form = _CTX
    return score(params[i // len(trials)], trials[i % len(trials)], form)


def score_grid(params: list[Params], trials: list[Prepared], form: str, runner: Runner) -> list[list]:
    flat = runner.map(_cell, range(len(params) * len(trials)), (params, trials, form))
    n = len(trials)
    return [flat[k * n:(k + 1) * n] for k in range(len(params))]


def _prepare_seed(seed: int) -> Prepared:
    return _CTX._one(seed)


class FolderSource:
    """Trials from `<root>/seed-NN`. Memory: the 60 validation trials stay prepared for the run."""

    def __init__(self, root, plan: Plan) -> None:
        self.root, self.plan, self.shape = Path(root), plan, None

    def check(self, seeds) -> None:
        """Every seed of a batch needs its manifest and summary; otherwise nothing is loaded."""
        for s in seeds:
            if s in HELD_OUT or s in HELD_OUT_2 or not any(s in r for r in self.plan.allowed):
                raise TrialError(f"seed {s}: not allowed here")
        if any(not (self.root / f"seed-{s:02d}" / f).is_file() for s in seeds for f in ("manifest.json", "summary.json")):
            raise NeedSeeds(min(seeds), max(seeds))

    def _one(self, seed: int) -> Prepared:
        return prepare(load_seed(self.root, seed, self.plan.allowed, self.shape))

    def prepared(self, seeds, runner: Runner) -> list[Prepared]:
        if self.shape is None:  # the first validation trial's days, speed and scenario hash
            self.shape = manifest_shape(self.root, self.plan.val_seeds[0], self.plan.allowed)
        return runner.map(_prepare_seed, seeds, self)


# --- the inner loop ---------------------------------------------------------------------------

def _normal(rng: SplitMix64) -> float:
    u1 = (rng.next() >> 11) / (1 << 53)
    u2 = (rng.next() >> 11) / (1 << 53)
    return math.sqrt(-2.0 * math.log(1.0 - u1)) * math.cos(2.0 * math.pi * u2)


def sample(mu: Params, sigma: dict, rng: SplitMix64, cfg: dict, frozen: frozenset) -> Params:
    """Draws are always taken in the same order, so a frozen weight or a fixed theta changes only
    that coordinate, not the stream."""
    w = []
    for name, x in zip(NAMES, mu.weights):
        z = _normal(rng)
        w.append(0.0 if name in frozen else round(max(0.0, x + sigma["w"] * z), 4))
    z = _normal(rng)
    theta = mu.theta if cfg["fix_theta"] else round(max(THETA_MIN, mu.theta + sigma["theta"] * z), 4)
    p = min(P_MAX, max(1, round(mu.p + sigma["p"] * _normal(rng))))
    return Params(tuple(w), theta, p)


def generation_seeds(start: int, sel: int, conf: int, g: int) -> tuple[list[int], list[int], int]:
    """Selection seeds, then the fresh confirmation seeds above them, and the next free seed."""
    a = start + (sel + conf) * (g - 1)
    return list(range(a, a + sel)), list(range(a + sel, a + sel + conf)), a + sel + conf


def confirm_test(d: list[float]) -> tuple[bool, float, float, float]:
    """Accept when mean(d) > t x stdev(d) / sqrt(n), strictly; stdev 0 makes it mean(d) > 0."""
    n = len(d)
    mean, sd = sum(d) / n, statistics.stdev(d)
    threshold = T_CRIT[n] * sd / math.sqrt(n)
    return mean > threshold, mean, sd, threshold


def advance(best: float | None, best_gen: int | None, patience: int, g: int, reward: float | None,
            plan: Plan) -> tuple[float | None, int | None, int, str | None]:
    """Validation bookkeeping after generation g >= 1: strict improvement resets the counter."""
    if reward is not None and (best is None or reward > best):
        best, best_gen, patience = reward, g, 0
    else:
        patience += 1
    stopped = "patience" if patience >= plan.patience else "max_generations" if g >= plan.max_generations else None
    return best, best_gen, patience, stopped


def _r6(x: float | None) -> float | None:
    return None if x is None else round(x, 6)


def _mean(scores: list) -> float:
    return sum(s.reward for s in scores) / len(scores)


def _val(params: Params, val: list[Prepared], form: str, runner: Runner) -> dict:
    scores = score_grid([params], val, form, runner)[0]
    rejected = any(s.rejected for s in scores)
    return {"seeds": [t.seed for t in val], "reward": None if rejected else round(_mean(scores), 6),
            "rejected_time_in_alert": rejected,
            "per_seed": [{"seed": t.seed, "reward": round(s.reward, 6), "caught_early": s.caught_early,
                          "episodes": t.episodes, "alerts_per_patient_12h": round(s.alerts_per_12h, 6),
                          "time_in_alert": round(s.time_in_alert, 6),
                          "news2_time_in_alert": round(t.news2_on / t.cohort_rows, 6)}
                         for t, s in zip(val, scores)]}


def step(cur: Params, sigma: dict, cfg: dict, frozen: frozenset, plan: Plan, rnd: int, g: int,
         sel: list[Prepared], conf: list[Prepared], runner: Runner) -> tuple[dict, Params, dict]:
    """One generation's search and acceptance: the part of the record before validation."""
    form = cfg["form"]
    rng = SplitMix64(SEED + 1000 * rnd + g)
    cands = [sample(cur, sigma, rng, cfg, frozen) for _ in range(plan.candidates)]
    grid = score_grid([cur, *cands], sel, form, runner)
    cur_mean = _mean(grid[0])
    winner, win_mean, dropped = None, None, 0
    for c, scores in zip(cands, grid[1:]):
        if any(s.rejected for s in scores):
            dropped += 1
            continue
        m = _mean(scores)
        if winner is None or m > win_mean:  # ties go to the first drawn
            winner, win_mean = c, m
    selection = {"current_mean": _r6(cur_mean), "current_rejected": any(s.rejected for s in grid[0]),
                 "candidates": len(cands), "candidates_rejected": dropped,
                 "winner": None if winner is None else {"params": winner.as_dict(), "mean": _r6(win_mean)}}
    confirmation, accepted, reason = None, False, None
    if winner is None:
        reason = "no candidate left after the time-in-alert rule"
    elif not win_mean > cur_mean:
        reason = "winner not above the current policy on the selection seeds"
    else:
        # A winner that fails below is not replaced by the second best: that would reuse this batch.
        cg = score_grid([cur, winner], conf, form, runner)
        d = [round(w.reward - c.reward, 6) for c, w in zip(*cg)]
        passed, mean, sd, thr = confirm_test(d)
        wrej = any(s.rejected for s in cg[1])
        accepted = passed and not wrej
        reason = None if accepted else ("winner rejected for time in alert on a confirmation seed" if wrej
                                        else "mean(d) not above the threshold")
        confirmation = {"d": [{"seed": t.seed, "d": x} for t, x in zip(conf, d)], "mean": _r6(mean),
                        "stdev": _r6(sd), "threshold": _r6(thr), "winner_rejected": wrej,
                        "current_rejected": any(s.rejected for s in cg[0])}
    nxt = winner if accepted else cur
    shrink = 1.0 if accepted else cfg["shrink"]
    sigma_next = {k: round(max(SIGMA_FLOOR[k], v * shrink), 9) for k, v in sigma.items()}
    return ({"selection": selection, "confirmation": confirmation, "accepted": accepted, "reason": reason},
            nxt, sigma_next)


# --- files and resume -------------------------------------------------------------------------

@dataclass
class Spec:
    gen_dir: Path
    policy_path: Path
    round: int
    temperature: bool
    cfg: dict
    start: Params
    start_prev: str  # "none" or the sha256 of the starting policy file's bytes
    start_seed: int


def _head(spec: Spec, g: int) -> dict:
    return {"schema": SCHEMA, "round": spec.round, "generation": g, "temperature": spec.temperature,
            "synthetic": True, "validated": False, "config": spec.cfg, "config_sha256": digest(spec.cfg),
            "generator_seed": None if g == 0 else SEED + 1000 * spec.round + g}


def _seal(body: dict, prev: str) -> tuple[dict, bytes]:
    body = {**body, "prev_sha256": prev}
    rec = {**body, "sha256": digest(body)}
    return rec, _dump(rec)


GEN_FILE = re.compile(r"gen-([0-9]{2})\.json")


def read_chain(spec: Spec, plan: Plan) -> tuple[list[dict], str]:
    """Verify gen-00 to the last file: contiguous, hashed, linked, same config, same schedule."""
    names = []
    if spec.gen_dir.is_dir():
        for f in spec.gen_dir.iterdir():
            if f.name.endswith(".tmp"):
                f.unlink()
            elif f.name.startswith("gen-"):
                names.append(f.name)
    nums = []
    for n in sorted(names):
        m = GEN_FILE.fullmatch(n)
        if not m:
            raise refuse(f"{n}: not a generation file name")
        nums.append(int(m[1]))
    if nums != list(range(len(nums))):
        raise refuse("a generation file is missing (the numbers have a gap)")
    sel_n, records, prev = spec.cfg["selection_trials"], [], spec.start_prev
    for i in nums:
        name = f"gen-{i:02d}.json"
        raw = _read_file(spec.gen_dir / name)
        try:
            rec = json.loads(raw)
            body = {k: v for k, v in rec.items() if k != "sha256"}
            if digest(body) != rec["sha256"]:
                raise refuse(f"{name}: content does not match its sha256; refused")
            if rec["prev_sha256"] != prev:
                raise refuse(f"{name}: prev_sha256 does not match the previous file; refused")
            if (rec["schema"], rec["round"], rec["generation"]) != (SCHEMA, spec.round, i):
                raise refuse(f"{name}: schema, round or generation number differs from this run; refused")
            if rec["config"] != spec.cfg or rec["config_sha256"] != digest(spec.cfg):
                raise refuse(f"{name}: config differs from this run; refused")
            if rec["temperature"] is not spec.temperature:
                raise refuse(f"{name}: temperature flag differs from this run; refused")
            if i == 0:
                want = ([], [], spec.start_seed)
                if rec["params"] != spec.start.as_dict():
                    raise refuse(f"{name}: starting params differ from the starting policy; refused")
            else:
                want = generation_seeds(spec.start_seed, sel_n, plan.confirm, i)
            if (rec["selection_seeds"], rec["confirmation_seeds"], rec["next_seed"]) != want:
                raise refuse(f"{name}: seeds are not the schedule from seed {spec.start_seed}; refused")
            if (i < len(nums) - 1) and rec["stopped"] is not None:
                raise refuse(f"{name}: a file follows a stopped record; refused")
            Params.from_dict(rec["params"])
            [float(rec["sigma_next"][k]) for k in SIGMA_FLOOR]
            int(rec["patience"])
        except TrialError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError):
            raise refuse(f"{name}: malformed record; refused") from None
        records.append(rec)
        prev = sha(raw)
    return records, prev


def policy_body(spec: Spec, plan: Plan, records: list[dict]) -> dict | None:
    """The round's shipped policy: the generation that last improved on validation."""
    best = records[-1]["best_generation"]
    if best is None:
        return None
    rec = records[best]
    return {"name": "tier_b/reward_policy" + ("" if spec.temperature else "/no_temperature"),
            "round": spec.round, "temperature": spec.temperature, "synthetic": True, "validated": False,
            "config": spec.cfg, "config_sha256": digest(spec.cfg), "chosen_generation": best,
            "params": rec["params"], "validation_reward": rec["validation"]["reward"], "lambda": LAMBDA,
            "validation_seeds": list(plan.val_seeds),
            "validation_per_seed": [{"seed": x["seed"], "reward": x["reward"]} for x in rec["validation"]["per_seed"]],
            "seeds": {"first": spec.start_seed, "last": records[-1]["next_seed"] - 1},
            "stop_reason": records[-1]["stopped"],
            "curve": [{"generation": r["generation"], "accepted": r["accepted"],
                       "winner_selection_mean": ((r["selection"] or {}).get("winner") or {}).get("mean"),
                       "confirmation_mean_d": (r["confirmation"] or {}).get("mean"),
                       "validation_reward": r["validation"]["reward"], "patience": r["patience"]}
                      for r in records]}


def run_inner(spec: Spec, source, plan: Plan, runner: Runner, log=print) -> tuple[dict | None, str, list[dict]]:
    """Run (or resume) one inner loop. Returns the policy file's body with its sha256, that file's
    bytes sha256, and the records; the policy is None when no generation was within NEWS2's time
    in alert on validation."""
    records, prev = read_chain(spec, plan)
    form, frozen = spec.cfg["form"], frozen_set(spec.cfg, spec.temperature)
    sel_n = spec.cfg["selection_trials"]
    if not (records and records[-1]["stopped"]):
        source.check(plan.val_seeds)
        source.check(range(*_span(spec, plan, max(1, len(records)))))  # before the slow validation load
        val = source.prepared(plan.val_seeds, runner)
        if not records:
            v = _val(spec.start, val, form, runner)
            first = v["reward"]
            body = {**_head(spec, 0), "selection_seeds": [], "confirmation_seeds": [], "next_seed": spec.start_seed,
                    "sigma_used": None, "sigma_next": sigma_start(spec.cfg), "selection": None,
                    "confirmation": None, "accepted": None, "reason": None, "params": spec.start.as_dict(),
                    "validation": v, "best_validation": first, "best_generation": None if first is None else 0,
                    "patience": 0, "stopped": None}
            rec, raw = _seal(body, prev)
            _write(spec.gen_dir / "gen-00.json", raw)
            records, prev = [rec], sha(raw)
            log(f"round {spec.round} gen  0: validation {first}", flush=True)
        while records[-1]["stopped"] is None:
            last, g = records[-1], len(records)
            sel_s, conf_s, nxt = generation_seeds(spec.start_seed, sel_n, plan.confirm, g)
            source.check(sel_s + conf_s)
            loaded = source.prepared(sel_s + conf_s, runner)
            sigma = {k: float(last["sigma_next"][k]) for k in SIGMA_FLOOR}
            cur = Params.from_dict(last["params"])
            fields, new, sigma_next = step(cur, sigma, spec.cfg, frozen, plan, spec.round, g,
                                           loaded[:sel_n], loaded[sel_n:], runner)
            v = _val(new, val, form, runner)
            best, best_gen, patience, stopped = advance(last["best_validation"], last["best_generation"],
                                                        last["patience"], g, v["reward"], plan)
            body = {**_head(spec, g), "selection_seeds": sel_s, "confirmation_seeds": conf_s, "next_seed": nxt,
                    "sigma_used": {k: round(x, 9) for k, x in sigma.items()}, "sigma_next": sigma_next,
                    **fields, "params": new.as_dict(), "validation": v, "best_validation": best,
                    "best_generation": best_gen, "patience": patience, "stopped": stopped}
            rec, raw = _seal(body, prev)
            _write(spec.gen_dir / f"gen-{g:02d}.json", raw)
            records.append(rec)
            prev = sha(raw)
            sel, cf = fields["selection"], fields["confirmation"]
            log(f"round {spec.round} gen {g:2d}: seeds {sel_s[0]}-{conf_s[-1]}; selection winner "
                f"{(sel['winner'] or {}).get('mean')} vs current {sel['current_mean']}; confirmation mean d "
                f"{None if cf is None else cf['mean']}; accepted {fields['accepted']}; validation {v['reward']}; "
                f"best {best} (gen {best_gen}); patience {patience}; stopped {stopped}", flush=True)
    body = policy_body(spec, plan, records)
    if body is None:
        return None, "", records
    pol = {**body, "sha256": digest(body)}
    raw = _dump(pol)
    _write(spec.policy_path, raw)
    return pol, sha(raw), records


def _span(spec: Spec, plan: Plan, g: int) -> tuple[int, int]:
    s, c, _ = generation_seeds(spec.start_seed, spec.cfg["selection_trials"], plan.confirm, g)
    return s[0], (c[-1] + 1)


# --- the policy files -------------------------------------------------------------------------

def load_policy(path: Path, temperature: bool) -> tuple[dict, str]:
    """The policy file as an object and the sha256 of its bytes; refused when its content does not
    match its own sha256."""
    try:
        data = _read_file(path)
        obj = json.loads(data)
    except (TrialError, ValueError, RecursionError):
        raise TrialError(f"{path.name}: cannot be read as JSON (run the loop first)") from None
    if not isinstance(obj, dict) or "sha256" not in obj:
        raise TrialError(f"{path.name}: not a policy file")
    body = {k: v for k, v in obj.items() if k != "sha256"}
    if digest(body) != obj["sha256"]:
        raise TrialError(f"{path.name}: content does not match its sha256; refused")
    if obj.get("temperature") is not temperature:
        raise TrialError(f"{path.name}: temperature flag does not match its folder; refused")
    try:
        params = Params.from_dict(obj["params"])
        check_config(obj["config"])
        if digest(obj["config"]) != obj["config_sha256"]:
            raise TrialError(f"{path.name}: config does not match config_sha256; refused")
    except TrialError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as e:
        raise TrialError(f"{path.name}: bad params ({type(e).__name__})") from None
    if not temperature and params.weights[3] != 0.0:
        raise TrialError(f"{path.name}: no-temperature policy with a temperature weight; refused")
    return obj, sha(data)


def read_policy(path: Path, temperature: bool) -> Params:
    """A current-format policy (has `config`) is checked by load_policy; a legacy B6 policy (the
    research agent's) by reward_loop_b6.read_policy."""
    try:
        obj = json.loads(_read_file(path))
    except (TrialError, ValueError, RecursionError):
        raise TrialError(f"{path.name}: cannot be read as JSON (run the loop first)") from None
    if isinstance(obj, dict) and "config" not in obj:
        return reward_loop_b6.read_policy(path, temperature)
    return Params.from_dict(load_policy(path, temperature)[0]["params"])


# --- the ledger -------------------------------------------------------------------------------

def _line(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def read_ledger(path: Path) -> list[dict]:
    """Every line canonical and chained by `prev_line_sha256`; rounds in order; stop, then at most
    one no_temperature record."""
    if not path.exists():
        return []
    text = _read_file(path).decode("utf-8", "replace")
    lines = text.split("\n")
    if lines.pop() != "":
        raise refuse(f"{LEDGER}: does not end with a newline; refused")
    out, prev, rounds, stage = [], "none", 0, 0
    for n, ln in enumerate(lines, 1):
        try:
            obj = json.loads(ln)
        except (ValueError, RecursionError):
            raise refuse(f"{LEDGER} line {n}: not JSON; refused") from None
        if not isinstance(obj, dict) or _line(obj) != ln or obj.get("prev_line_sha256") != prev:
            raise refuse(f"{LEDGER} line {n}: not canonical or its link is broken; refused")
        d = obj.get("decision")
        if d in ("baseline", "kept", "dropped"):
            first = rounds == 0
            if stage or obj.get("round") != rounds + 1 or (d == "kept" and first) or (d == "baseline" and not first):
                raise refuse(f"{LEDGER} line {n}: out of order; refused")
            rounds += 1
        elif d == "stop" and not stage and rounds:
            stage = 1
        elif d == "no_temperature" and stage == 1:
            stage = 2
        else:
            raise refuse(f"{LEDGER} line {n}: out of order; refused")
        out.append(obj)
        prev = sha(ln)
    return out


def append_line(path: Path, obj: dict) -> None:
    led = read_ledger(path)
    prev = sha(_line(led[-1])) if led else "none"
    old = path.read_bytes() if path.exists() else b""
    _write(path, old + (_line({**obj, "prev_line_sha256": prev}) + "\n").encode())


def _same(a: dict, b: dict) -> bool:
    drop = lambda o: {k: v for k, v in o.items() if k != "prev_line_sha256"}  # noqa: E731
    return drop(a) == drop(b)


def incumbent(led: list[dict]) -> dict | None:
    kept = [x for x in led if x.get("decision") in ("baseline", "kept")]
    return kept[-1] if kept else None


def refresh_top(out: Path, led: list[dict]) -> None:
    """The top-level policy.json: the incumbent's round policy, same bytes."""
    inc = incumbent(led)
    if inc is not None:
        _write(out / "policy.json", _read_file(out / "rounds" / f"round-{inc['round']}" / "policy.json"))


def _shipped(pol: dict | None, file_sha: str) -> dict | None:
    return None if pol is None else {"generation": pol["chosen_generation"], "params": pol["params"],
                                     "validation_reward": pol["validation_reward"], "policy_sha256": file_sha}


# --- the outer loop ---------------------------------------------------------------------------

def check_plan(plan: Plan) -> None:
    if plan.confirm not in T_CRIT:
        raise refuse(f"confirmation size {plan.confirm}: no t value for it (2 to {max(T_CRIT)} trials)")


def run_round(out: Path, rnd: int, source, plan: Plan, runner: Runner, change: str | None = None,
              why: str | None = None, log=print) -> None:
    check_plan(plan)
    led = read_ledger(out / LEDGER)
    judged = [x for x in led if x.get("decision") in ("baseline", "kept", "dropped")]
    if rnd > len(judged) and any(x.get("decision") == "stop" for x in led):
        raise refuse("the ledger has its stop line; no round may start")
    if not 1 <= rnd <= plan.max_rounds or rnd > len(judged) + 1:
        raise refuse(f"round {rnd}: rounds run in order from 1 to {plan.max_rounds}, each after the one before is judged")
    inc, inc_pol, inc_sha = incumbent(judged[:rnd - 1]), None, ""
    if rnd == 1:
        if change is not None or why is not None:
            raise refuse("round 1 is the baseline: no --change and no --why")
        cfg, chg, why = check_config(dict(plan.base)), None, "baseline: the default configuration"
        start, prev, seed0 = generation_zero(True), "none", plan.first_seed
    else:
        if inc is None:
            raise refuse("no incumbent: round 1 shipped nothing")
        if change is None or why is None:
            raise refuse(f"round {rnd}: needs exactly one --change key=value and a --why")
        why = _text(why, "--why")
        inc_pol, inc_sha = load_policy(out / "rounds" / f"round-{inc['round']}" / "policy.json", True)
        if inc_sha != inc["shipped"]["policy_sha256"]:
            raise refuse("the incumbent's policy file does not match the ledger; refused")
        cfg, chg = parse_change(change, inc_pol["config"])
        start, prev, seed0 = Params.from_dict(inc_pol["params"]), inc_sha, judged[rnd - 2]["seeds"]["last"] + 1
    start = start_params(start, frozen_set(cfg, True))
    # A round that already has generation files was checked when it started (rounds 1 and 2 began under the
    # old 401-2800 budget): resuming or verifying it never refuses for the budget.
    started = any((out / "rounds" / f"round-{rnd}").glob("gen-*.json"))
    if not started and seed0 + plan.max_generations * (cfg["selection_trials"] + plan.confirm) - 1 > plan.last_seed:
        raise refuse(f"budget: a full {plan.max_generations}-generation round from seed {seed0} no longer fits; "
                     "append the stop line")
    spec = Spec(out / "rounds" / f"round-{rnd}", out / "rounds" / f"round-{rnd}" / "policy.json", rnd, True,
                cfg, start, prev, seed0)
    pol, file_sha, records = run_inner(spec, source, plan, runner, log)
    vs, decision = None, "baseline" if rnd == 1 else "dropped"
    if pol is None:
        decision = "dropped"
    elif rnd > 1:
        a, b = pol["validation_per_seed"], inc_pol["validation_per_seed"]
        if [x["seed"] for x in a] != [x["seed"] for x in b]:
            raise refuse("the round and the incumbent were not scored on the same validation seeds")
        v = [x["reward"] - y["reward"] for x, y in zip(a, b)]
        mean, sd = sum(v) / len(v), statistics.stdev(v)
        margin = KEEP_SE * sd / math.sqrt(len(v))
        vs = {"mean": _r6(mean), "stdev": _r6(sd), "margin": _r6(margin)}
        decision = "kept" if mean > margin else "dropped"
    line = {"round": rnd, "decision": decision, "why": why, "change": chg, "config_sha256": digest(cfg),
            "started_from": None if inc is None else {"round": inc["round"], "policy_sha256": inc_sha,
                                                      "validation_reward": inc_pol["validation_reward"]},
            "seeds": {"first": seed0, "last": records[-1]["next_seed"] - 1}, "generations": len(records) - 1,
            "stop_reason": records[-1]["stopped"], "shipped": _shipped(pol, file_sha), "vs_incumbent": vs}
    if rnd <= len(judged):
        if not _same(judged[rnd - 1], line):
            raise refuse(f"round {rnd}: the ledger line differs from this run; refused")
    else:
        append_line(out / LEDGER, line)
    refresh_top(out, read_ledger(out / LEDGER))
    log(f"round {rnd}: {decision}; shipped {None if pol is None else pol['chosen_generation']}", flush=True)


def stop(out: Path, reason: str) -> None:
    reason = _text(reason, "--stop")
    led = read_ledger(out / LEDGER)
    if not any(x.get("decision") in ("baseline", "kept", "dropped") for x in led):
        raise refuse("no round is judged yet")
    line = {"decision": "stop", "reason": reason}
    old = [x for x in led if x.get("decision") == "stop"]
    if old and not _same(old[0], line):
        raise refuse("the ledger already has a different stop line")
    if not old:
        append_line(out / LEDGER, line)
    refresh_top(out, led)


def run_no_temperature(out: Path, source, plan: Plan, runner: Runner, log=print) -> None:
    check_plan(plan)
    led = read_ledger(out / LEDGER)
    if not any(x.get("decision") == "stop" for x in led):
        raise refuse("the ledger has no stop line yet; the no-temperature run comes after it")
    inc = incumbent(led)
    if inc is None:
        raise refuse("no incumbent configuration to learn with")
    cfg = load_policy(out / "rounds" / f"round-{inc['round']}" / "policy.json", True)[0]["config"]
    start = start_params(generation_zero(False), frozen_set(cfg, False))
    spec = Spec(out / NO_TEMP_DIR / "generations", out / NO_TEMP_DIR / "policy.json", 1, False, cfg, start, "none",
                plan.first_seed)
    pol, file_sha, records = run_inner(spec, source, plan, runner, log)
    line = {"decision": "no_temperature", "config_sha256": digest(cfg),
            "seeds": {"first": plan.first_seed, "last": records[-1]["next_seed"] - 1},
            "generations": len(records) - 1, "stop_reason": records[-1]["stopped"], "shipped": _shipped(pol, file_sha)}
    old = [x for x in led if x.get("decision") == "no_temperature"]
    if old and not _same(old[0], line):
        raise refuse("the ledger's no_temperature record differs from this run; refused")
    if not old:
        append_line(out / LEDGER, line)
    log(f"no temperature: shipped {None if pol is None else pol['chosen_generation']}", flush=True)


# --- held-out, once ---------------------------------------------------------------------------

def _held_out_folders(root, held_out_set: int = 1) -> list[str]:
    """Only `seed-11` to `seed-30` (set 2: `seed-1001` to `seed-1400`) by name, each with its manifest read
    first and naming that seed; a wrong folder (ward-v2-ext) has none and is refused before any cohort file
    is opened."""
    found = []
    for s in evaluate.held_range(held_out_set):
        folder = Path(root) / f"seed-{s:02d}"
        if not folder.is_dir():
            continue
        if _int_field(_json(folder, "manifest.json"), "seed", "manifest.json", 0, 2**31 - 1) != s:
            raise TrialError(f"seed-{s:02d}: manifest names another seed; refused")
        found.append(str(folder))
    if not found:
        raise TrialError("no held-out trials: needs seed-11 to seed-30 folders" if held_out_set == 1 else
                         "no held-out trials: needs seed-1001 to seed-1400 folders")
    return found


def evaluate_held_out(root: str, out: Path, result: dict | None = None, held_out_set: int = 1) -> str:
    led = read_ledger(out / LEDGER)
    if not any(x.get("decision") == "stop" for x in led):
        raise refuse("the ledger has no stop line; the held-out run comes last")
    rows = []
    for tag, folder, temp in (("", out, True), ("_no_temp", out / NO_TEMP_DIR, False)):
        obj, file_sha = load_policy(folder / "policy.json", temp)
        inc = incumbent(led)
        if temp and (inc is None or inc["shipped"]["policy_sha256"] != file_sha):
            raise refuse("policy.json is not the ledger's current incumbent policy; refused")
        # Generation 0 is always the fixed start of the learning curve (default configuration).
        rows += [*policies(f"reward_gen0{tag}", generation_zero(temp)),
                 *policies(f"reward_final{tag}", Params.from_dict(obj["params"]), obj["config"]["form"])]
    b4, _ = evaluate._baseline_params()
    k, p = b4.k, b4.p
    pols = (POLICIES[0], *baseline_policies(k, p), *baseline_policies(k, p, "_no_temp", NO_TEMP), *rows)
    return evaluate.run(_held_out_folders(root, held_out_set), pols, result, held_out_set, _POLICY_MEMO.clear)


# --- the run log: committed records only, no trial is opened ---------------------------------------

RUN_FLAGS = re.compile(r"[A-Za-z0-9._-]{1,64}")
COMMIT = re.compile(r"[\x21-\x7e]{1,128}")


def _span_of(seeds: list) -> dict | None:
    return {"first": seeds[0], "last": seeds[-1]} if seeds else None


def _generation_line(r: dict) -> dict:
    sel, cf = r["selection"], r["confirmation"]
    win = None if sel is None else sel["winner"]
    return {"generation": r["generation"], "accepted": r["accepted"], "reason": r["reason"],
            "selection_seeds": _span_of(r["selection_seeds"]), "confirmation_seeds": _span_of(r["confirmation_seeds"]),
            "sigma_used": r["sigma_used"], "sigma_next": r["sigma_next"],
            "selection": None if sel is None else {
                "current_mean": sel["current_mean"], "current_rejected": sel["current_rejected"],
                "candidates": sel["candidates"], "candidates_rejected": sel["candidates_rejected"],
                "winner_mean": None if win is None else win["mean"]},
            "confirmation": None if cf is None else {
                "n": len(cf["d"]), "mean_d": cf["mean"], "stdev_d": cf["stdev"], "threshold": cf["threshold"],
                "winner_rejected": cf["winner_rejected"]},
            "params": r["params"], "validation_reward": r["validation"]["reward"],
            "validation_rejected": r["validation"]["rejected_time_in_alert"],
            "best_validation": r["best_validation"], "best_generation": r["best_generation"],
            "patience": r["patience"], "stopped": r["stopped"]}


def _read_generations(folder: Path) -> list[dict]:
    """The generation records of one inner run, each checked against its own sha256 and the numbering."""
    names = sorted(f.name for f in folder.iterdir() if GEN_FILE.fullmatch(f.name)) if folder.is_dir() else []
    if not names or names != [f"gen-{i:02d}.json" for i in range(len(names))]:
        raise refuse(f"{folder.name}: generation files are missing or have a gap")
    out = []
    for n in names:
        try:
            rec = json.loads(_read_file(folder / n))
            if digest({k: v for k, v in rec.items() if k != "sha256"}) != rec["sha256"]:
                raise refuse(f"{n}: content does not match its sha256; refused")
            out.append(_generation_line(rec) | {"temperature": rec["temperature"], "config": rec["config"]})
        except TrialError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            raise refuse(f"{n}: malformed record; refused") from None
    return out


def run_log(out: Path, run_id: str | None, commit: str | None) -> dict:
    """One small JSON for the whole of M2: the ledger lines and, per round and for the no-temperature
    run, the numbers of every generation. Everything comes from files already committed; there is no
    wall time to record, and the `time` object says so."""
    if run_id is not None and not RUN_FLAGS.fullmatch(run_id):
        raise refuse("--run-id may only hold A-Z a-z 0-9 . _ - and at most 64 characters")
    if commit is not None and not COMMIT.fullmatch(commit):
        raise refuse("--commit must be 1 to 128 printable characters without spaces")
    led = read_ledger(out / LEDGER)
    if not any(x.get("decision") == "stop" for x in led):
        raise refuse("the ledger has no stop line; the run is not finished")
    runs = []
    for x in led:
        d = x["decision"]
        if d in ("baseline", "kept", "dropped"):
            folder = out / "rounds" / f"round-{x['round']}"
        elif d == "no_temperature":
            folder = out / NO_TEMP_DIR / "generations"
        else:
            continue
        gens = _read_generations(folder)
        runs.append({"name": f"round {x['round']}" if "round" in x else "no temperature", "temperature": gens[0]["temperature"],
                     "config": gens[0]["config"], "ledger": x, "generations": [{k: v for k, v in g.items()
                                                                                if k not in ("temperature", "config")} for g in gens]})
    first = json.loads(_read_file(out / "policy.json"))
    return {"model": "tier_b/reward_policy", "run_id": run_id, "commit": commit,
            "framework": "none (six numbers, plain arithmetic)", "framework_version": None,
            "settings": {"search": "(1+30) evolution strategy with a reward; not deep or per-step reinforcement learning",
                         "lambda": first["lambda"], "generator_seed": SEED, "candidates": 30,
                         "validation_seeds": _span_of(first["validation_seeds"]),
                         "confirmation_trials": 12, "patience": 3, "max_generations": 40},
            "stop": next(x for x in led if x["decision"] == "stop"), "runs": runs,
            "time": {"recorded": False, "note": "no wall time: this log is built from committed records, no trial was opened"}}


# --- the command ------------------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise TrialError(f"usage: {message} (see research/README.md)")


def main(argv: list[str], *, plan: Plan | None = None, source=None) -> int:
    if "--train" in argv or "--validate" in argv:  # the legacy B6 command, used by research/agent/
        return reward_loop_b6.main(argv)
    plan = plan or Plan()
    ap = _Parser(prog="research.reward_loop", add_help=False, allow_abbrev=False)
    ap.add_argument("--out", required=True)
    for a in ("--data", "--evaluate", "--change", "--why", "--stop", "--run-log", "--run-id", "--commit", "--json",
              "--held-out-set"):
        ap.add_argument(a)
    ap.add_argument("--round", type=int)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--no-temperature", action="store_true")
    try:
        a = ap.parse_args(argv)
        out, runner = Path(a.out), Runner(a.jobs)
        if not 1 <= a.jobs <= 64:
            raise refuse("--jobs: 1 to 64")
        modes = [a.evaluate is not None, a.stop is not None, a.no_temperature, a.round is not None, a.run_log is not None]
        if sum(modes) != 1:
            raise refuse("usage: exactly one of --round, --no-temperature, --stop, --evaluate, --run-log (see research/README.md)")
        if a.json is not None and a.evaluate is None:
            raise refuse("--json goes with --evaluate")
        if a.held_out_set is not None and a.evaluate is None:
            raise refuse("--held-out-set goes with --evaluate")
        if (a.run_id is not None or a.commit is not None) and a.run_log is None:
            raise refuse("--run-id and --commit go with --run-log")
        if a.run_log is not None:
            _write(Path(a.run_log), _dump(run_log(out, a.run_id, a.commit)))
        elif a.evaluate is not None:
            evaluate.check_json_path(a.json)  # before any trial is read
            result: dict = {}
            text = evaluate_held_out(a.evaluate, out, result if a.json else None,
                                     evaluate.check_set(a.held_out_set or 1))
            if a.json:
                evaluate.write_json(a.json, result)
            sys.stdout.write(text)
        elif a.stop is not None:
            stop(out, a.stop)
        else:
            if source is None:
                if a.data is None:
                    raise refuse("--data is needed")
                source = FolderSource(a.data, plan)
            if a.no_temperature:
                run_no_temperature(out, source, plan, runner)
            else:
                run_round(out, a.round, source, plan, runner, a.change, a.why)
    except NeedSeeds as e:
        print(e, file=sys.stderr)
        return 3
    except TrialError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    except OSError:
        print("refused: cannot read or write --out", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
