"""LEGACY. The B6 reward loop: 10 generations of 3 trials each, validated on 3 trials. Its own card
called its path mostly noise. It is kept only because research/agent/ still drives it. The current
loop is research/reward_loop.py; moving the agent to the current loop is a follow-up for the
agent's owner.

    python -m research.reward_loop --train <ward-v2-train> --validate <ward-v2> --out <dir> [--no-temperature]
    python -m research.reward_loop --evaluate <ward-v2> --out <dir>

The rules are in research/README.md. Training reads only seeds 31 to 60 of --train and
validation only seeds 8 to 10 of --validate; seeds 11 to 30 are refused before any file but the
manifest is opened. --evaluate scores held-out seeds 11 to 30 once. Exit 2 on a refusal.
"""

import hashlib
import json
import math
import os
import sys
from pathlib import Path

from . import evaluate
from .alerts import POLICIES
from .baseline_policy import NO_TEMP, baseline_policies
from .bootstrap import SplitMix64
from .reward_policy import LAMBDA, P_MAX, THETA_MIN, Params, Prepared, generation_zero, mean_reward, policies, prepare
from .rescore import check_parity
from .trials import Trial, TrialError, _int_field, _json, load_trial

GENERATIONS = 10
PER_GENERATION = 3
TRAIN_SEEDS = range(31, 31 + GENERATIONS * PER_GENERATION)
VAL_SEEDS = range(8, 11)
HELD_OUT = evaluate.HELD_OUT
CANDIDATES = 30
SIGMA = {"w": 0.5, "theta": 0.5, "p": 1.0}
SHRINK = 0.9
SEED = 20261112
NO_TEMP_DIR = "no_temperature"


def generation_seeds(g: int) -> list[int]:
    return [TRAIN_SEEDS.start + (g - 1) * PER_GENERATION + i for i in range(PER_GENERATION)]


# --- the split, enforced where data is read ---------------------------------------------------

def load_seed(root: str | os.PathLike, seed: int, allowed: range) -> Trial:
    """Open `<root>/seed-NN` for an allowed seed. The manifest is read first; a held-out seed or a
    manifest that names another seed is refused before cohort.csv or episodes.csv is opened."""
    if seed in HELD_OUT or seed not in allowed:
        raise TrialError(f"seed {seed}: not allowed here")
    folder = Path(root) / f"seed-{seed:02d}"
    if not folder.is_dir():
        raise TrialError(f"seed-{seed:02d}: missing")
    found = _int_field(_json(folder, "manifest.json"), "seed", "manifest.json", 0, 2**31 - 1)
    if found != seed or found in HELD_OUT:
        raise TrialError(f"seed-{seed:02d}: manifest says seed {found}; refused")
    trial = load_trial(folder)
    if trial.seed != seed:
        raise TrialError(f"seed-{seed:02d}: seed changed while reading")
    check_parity(trial)
    return trial


# --- the loop -----------------------------------------------------------------------------------

def _normal(rng: SplitMix64) -> float:
    u1 = (rng.next() >> 11) / (1 << 53)
    u2 = (rng.next() >> 11) / (1 << 53)
    return math.sqrt(-2.0 * math.log(1.0 - u1)) * math.cos(2.0 * math.pi * u2)


def sample(mu: Params, sigma: dict, rng: SplitMix64, temperature: bool) -> Params:
    w = [round(max(0.0, x + sigma["w"] * _normal(rng)), 4) for x in mu.weights]
    if not temperature:
        w[3] = 0.0  # never sampled: exactly 0
    theta = round(max(THETA_MIN, mu.theta + sigma["theta"] * _normal(rng)), 4)
    p = min(P_MAX, max(1, round(mu.p + sigma["p"] * _normal(rng))))
    return Params(tuple(w), theta, p)


def _val(params: Params, val: list[Prepared]) -> dict:
    r, scores = mean_reward(params, val)
    return {"seeds": [t.seed for t in val], "reward": None if r is None else round(r, 6),
            "rejected_time_in_alert": r is None,
            "per_seed": [{"seed": t.seed, "reward": round(s.reward, 6), "caught_early": s.caught_early,
                          "episodes": t.episodes, "alerts_per_patient_12h": round(s.alerts_per_12h, 6),
                          "time_in_alert": round(s.time_in_alert, 6),
                          "news2_time_in_alert": round(t.news2_on / t.cohort_rows, 6)}
                         for t, s in zip(val, scores)]}


def learn(train_for, val: list[Prepared], temperature: bool, generations: int = GENERATIONS,
          candidates: int = CANDIDATES):
    """Yield one record per generation (0 first). `train_for(g)` gives generation g's trials."""
    current = generation_zero(temperature)
    sigma = dict(SIGMA)
    yield {"generation": 0, "params": current.as_dict(), "validation": _val(current, val)}
    for g in range(1, generations + 1):
        trials = train_for(g)
        rng = SplitMix64(SEED + g)
        cur_r, _ = mean_reward(current, trials)
        best, best_r, rejected = None, None, 0
        for _ in range(candidates):
            cand = sample(current, sigma, rng, temperature)
            r, _ = mean_reward(cand, trials)
            if r is None:
                rejected += 1
            elif best_r is None or r > best_r:
                best, best_r = cand, r
        accepted = best_r is not None and (cur_r is None or best_r > cur_r)
        record = {
            "generation": g, "train_seeds": [t.seed for t in trials], "generator_seed": SEED + g,
            "sigma": {k: round(v, 6) for k, v in sigma.items()}, "candidates": candidates,
            "rejected_time_in_alert": rejected,
            "current_before": {"params": current.as_dict(), "train_reward": None if cur_r is None else round(cur_r, 6)},
            "best_candidate": None if best is None else {"params": best.as_dict(), "train_reward": round(best_r, 6)},
            "accepted": accepted,
        }
        if accepted:
            current = best
        record["params"] = current.as_dict()
        record["validation"] = _val(current, val)
        sigma = {k: v * SHRINK for k, v in sigma.items()}
        yield record


def choose(records: list[dict]) -> dict | None:
    ok = [r for r in records if r["validation"]["reward"] is not None]
    return max(ok, key=lambda r: (r["validation"]["reward"], -r["generation"]), default=None)


def digest(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def policy_file(records: list[dict], temperature: bool) -> dict | None:
    best = choose(records)
    if best is None:
        return None
    body = {
        "name": "tier_b/reward_policy" + ("" if temperature else "/no_temperature"),
        "temperature": temperature, "synthetic": True, "validated": False,
        "chosen_generation": best["generation"], "params": best["params"],
        "validation_reward": best["validation"]["reward"], "lambda": LAMBDA,
        "train_seeds": [s for r in records for s in r.get("train_seeds", [])],
        "validation_seeds": list(VAL_SEEDS),
        "curve": [{"generation": r["generation"],
                   "train_reward_before": r.get("current_before", {}).get("train_reward"),
                   "train_reward_best": (r.get("best_candidate") or {}).get("train_reward"),
                   "accepted": r.get("accepted"), "validation_reward": r["validation"]["reward"]}
                  for r in records],
    }
    return {**body, "sha256": digest(body)}


def _dump(obj: dict) -> bytes:
    return (json.dumps(obj, indent=1, sort_keys=True) + "\n").encode()


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def run_loop(train_root: str, val_root: str, out: Path, temperature: bool) -> list[dict]:
    if not temperature:
        out = out / NO_TEMP_DIR
    # Every seed must load before anything is learned: no training on a partial set.
    for s in TRAIN_SEEDS:
        if not (Path(train_root) / f"seed-{s:02d}" / "summary.json").is_file():
            raise TrialError(f"seed-{s:02d}: missing or incomplete")
    val = [prepare(load_seed(val_root, s, VAL_SEEDS)) for s in VAL_SEEDS]
    train_for = lambda g: [prepare(load_seed(train_root, s, TRAIN_SEEDS)) for s in generation_seeds(g)]  # noqa: E731
    records = []
    for rec in learn(train_for, val, temperature):
        rec = {**rec, "temperature": temperature, "synthetic": True, "validated": False}
        _write(out / "generations" / f"gen-{rec['generation']:02d}.json", _dump(rec))
        records.append(rec)
        v = rec["validation"]["reward"]
        print(f"gen {rec['generation']:2d}: train before {rec.get('current_before', {}).get('train_reward')}, "
              f"best {(rec.get('best_candidate') or {}).get('train_reward')}, accepted {rec.get('accepted')}; "
              f"validation {v}", flush=True)
    pol = policy_file(records, temperature)
    if pol is None:
        raise TrialError("no generation is within NEWS2's time in alert on validation: nothing chosen")
    _write(out / "policy.json", _dump(pol))
    print(f"chosen generation {pol['chosen_generation']}: {pol['params']}; sha256 {pol['sha256']}")
    return records


# --- held-out, once ---------------------------------------------------------------------------

MAX_POLICY_BYTES = 256 * 1024


def read_policy(path: Path, temperature: bool) -> Params:
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_POLICY_BYTES + 1)
        obj = json.loads(data)
    except (OSError, ValueError, RecursionError):
        raise TrialError(f"{path.name}: cannot be read as JSON (run the loop first)") from None
    if len(data) > MAX_POLICY_BYTES or not isinstance(obj, dict) or "sha256" not in obj:
        raise TrialError(f"{path.name}: not a policy file")
    body = {k: v for k, v in obj.items() if k != "sha256"}
    if digest(body) != obj["sha256"]:
        raise TrialError(f"{path.name}: content does not match its sha256; refused")
    if obj.get("temperature") is not temperature:
        raise TrialError(f"{path.name}: temperature flag does not match its folder; refused")
    try:
        params = Params.from_dict(obj["params"])
    except (KeyError, TypeError, ValueError, OverflowError) as e:
        raise TrialError(f"{path.name}: bad params ({type(e).__name__})") from None
    if not temperature and params.weights[3] != 0.0:
        raise TrialError(f"{path.name}: no-temperature policy with a temperature weight; refused")
    return params


def evaluate_held_out(root: str, out: Path) -> str:
    rows = []
    for tag, folder, temp in (("", out, True), ("_no_temp", out / NO_TEMP_DIR, False)):
        final = read_policy(folder / "policy.json", temp)
        rows += [*policies(f"reward_gen0{tag}", generation_zero(temp)), *policies(f"reward_final{tag}", final)]
    b4, _ = evaluate._baseline_params()
    k, p = b4.k, b4.p
    pols = (POLICIES[0], *baseline_policies(k, p), *baseline_policies(k, p, "_no_temp", NO_TEMP), *rows)
    folders = sorted(str(f) for f in Path(root).glob("seed-*") if f.is_dir())
    return evaluate.run(folders, pols)


def main(argv: list[str]) -> int:
    args, flags = {}, set()
    it = iter(argv)
    for a in it:
        if a in ("--train", "--validate", "--out", "--evaluate"):
            args[a] = next(it, None)
        elif a == "--no-temperature":
            flags.add(a)
        else:
            print(f"refused: unknown argument {a[:40]!r}", file=sys.stderr)
            return 2
    if None in args.values() or "--out" not in args:
        print("refused: usage in research/README.md", file=sys.stderr)
        return 2
    try:
        if "--evaluate" in args:
            sys.stdout.write(evaluate_held_out(args["--evaluate"], Path(args["--out"])))
        elif "--train" in args and "--validate" in args:
            run_loop(args["--train"], args["--validate"], Path(args["--out"]), "--no-temperature" not in flags)
        else:
            print("refused: usage in research/README.md", file=sys.stderr)
            return 2
    except TrialError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    except OSError:
        print("refused: cannot write to --out", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
