"""One round: read the ledger, ask the planner, run the tool, write the conclusion, maybe propose.

Conclusions and proposals come from the rules below, in code. The model, when on, only picks the
experiment.
"""

import hashlib
import json

from research import sweep

from . import ledger, tools
from .config import Config
from .ledger import LedgerError
from .planner import E1, E2, E3, E4, E5, state_from
from .tools import ToolError

GOAL = "Find out whether a better alerting rule exists on the simulated ward, using tuning seeds only."
MARGIN = 0.05  # fixed before any real run, not tuned afterwards
NOTE = "Point estimates on {n} episodes, no interval, simulated patients, a person decides."
APPROVE = 'python -m research.agent approve {id} --by "<name>"'


def _short(round_: dict) -> str:
    s, exp = round_, round_["experiment"]
    if not s["ok"]:
        return f"{s['id']} {exp}, the tool failed"
    if exp in (E1, E3):
        g = s["chosen_generation"]
        return f"{s['id']} {exp}, " + ("nothing chosen" if g is None else
                                       f"chosen generation {g}, validation reward {s['validation_reward']}")
    if exp in (E2, E4):
        return (f"{s['id']} {exp}, excess over chance {s['or_excess_over_chance']} "
                f"against {s['chart_excess_over_chance']} for the chart trigger")
    return f"{s['id']} {exp}, " + ("nothing chosen" if s["chosen_k"] is None else
                                   f"chosen k {s['chosen_k']}, p {s['chosen_p']}")


def _result_lines(exp: str, r: dict) -> list[str]:
    if exp in (E1, E3):
        if r["chosen_generation"] is None:
            return [f"exit code {r['exit_code']}, nothing chosen"]
        return [f"exit code {r['exit_code']}, {len(r['generations'])} generations",
                f"chosen generation {r['chosen_generation']}, validation reward {r['validation_reward']}",
                f"policy sha256 {r['policy_sha256'][:12]}"]
    if exp in (E2, E4):
        lines = [f"{r['episodes_not_cut_short']} episodes not cut short, {r['control_windows']} control windows, "
                 f"{r['patient_days']} patient-days"]
        for x in r["rules"]:
            lines.append(f"{x['name']}: {x['alerts']} alerts ({x['alerts_per_100_patient_days']} per 100 patient-days), "
                         f"caught {x['caught'][0]} of {x['caught'][1]}, chance {x['chance'][0]} of {x['chance'][1]}, "
                         f"excess over chance {x['excess_over_chance']}")
        return lines
    if r["chosen"] is None:
        return [f"{r['settings_tried']} settings tried, none inside the alert budget"]
    o, c = r["row"]["news2_or_baseline"], r["chart"]["news2_chart_trigger"]
    return [f"{r['settings_tried']} settings tried, chosen k {r['chosen']['k']}, p {r['chosen']['p']}",
            f"NEWS2 or baseline: {o['alerts']} alerts, excess over chance {o['excess_any_uncut']}; chart trigger: "
            f"{c['alerts']} alerts, excess over chance {c['excess_any_uncut']}"]


def _judge_check(cfg: Config, records: list[dict], exp: str, args: dict, r: dict):
    """The proposal rule after E2 or E4. Returns (conclusion, proposal or None)."""
    nt = exp == E4
    run = args["rules"][1]["run"]
    chart, either = r["rules"]
    budget = float(sweep.BUDGET)
    ok1 = either["alerts"] <= sweep.BUDGET * chart["alerts"]
    ok2 = round(either["excess_over_chance"] - chart["excess_over_chance"], 3) >= MARGIN
    parts = [f"alerts {either['alerts']} are {'at most' if ok1 else 'more than'} {budget} times the chart trigger's {chart['alerts']}",
             f"excess over chance {either['excess_over_chance']} is {'at least' if ok2 else 'not at least'} {MARGIN} "
             f"above the chart trigger's {chart['excess_over_chance']}"]
    new = next((_policy_sha(x) for x in records if x["type"] == "step" and x.get("id") == run), None)
    ok3 = False
    try:
        if new is None:
            parts.append(f"{run}'s policy hash is not in the ledger")
        else:
            rel = f"tier_b/reward_policy{'/no_temperature' if nt else ''}/policy.json"
            old = ledger.installed(cfg, rel)
            ok3 = old is None or hashlib.sha256(old).hexdigest() != new
            parts.append("no policy is installed" if old is None else
                         "the policy differs from the installed one" if ok3 else "the policy is the file already installed")
    except LedgerError as e:
        parts.append(f"the installed policy cannot be read ({e})")
    text = ("Proposal rule held: " if ok1 and ok2 and ok3 else "No proposal: ") + "; ".join(parts) + ". " \
        + NOTE.format(n=r["episodes_not_cut_short"])
    evidence = {"seeds": args["seeds"], "episodes_not_cut_short": r["episodes_not_cut_short"],
                "chart": {k: chart[k] for k in ("alerts", "caught", "chance", "excess_over_chance")},
                "or_rule": {k: either[k] for k in ("alerts", "caught", "chance", "excess_over_chance")},
                "policy_sha256": new, "note": NOTE.format(n=r["episodes_not_cut_short"])}
    return text, ("adopt_reward_policy", f"runs/{run}", nt, evidence) if ok1 and ok2 and ok3 else None


def _policy_sha(step: dict) -> str | None:
    sha = ((step.get("calls") or [{}])[0].get("result") or {}).get("policy_sha256")
    return sha if type(sha) is str else None


def _judge_sweep(cfg: Config, r: dict, sid: str):
    note = NOTE.format(n=r["chart"]["not_cut_short"])
    if r["chosen"] is None:
        return f"No proposal: no setting stayed inside the alert budget. {note}", None
    k, p = r["chosen"]["k"], r["chosen"]["p"]
    try:
        data = ledger.installed(cfg, "tier_b/baseline/params.json")
        if data is None:
            same, was = False, "no params.json is installed"
        else:
            try:
                old = json.loads(data)
                if type(old["k"]) not in (int, float) or type(old["p"]) is not int:
                    raise ValueError
            except (ValueError, KeyError, TypeError, RecursionError):
                return ("No proposal: the installed params.json cannot be read as an object with a numeric k "
                        f"and an integer p. {note}"), None
            same = (old["k"], old["p"]) == (k, p)
            was = f"the installed params.json has k {old['k']}, p {old['p']}"
    except LedgerError as e:
        return f"No proposal: the installed params.json cannot be read ({e}). {note}", None
    if same:
        return f"No proposal: the chosen k {k}, p {p} is what is already installed. {note}", None
    evidence = {"chosen": r["chosen"], "row": r["row"], "chart": r["chart"], "settings_tried": r["settings_tried"],
                "params_sha256": r["params_sha256"], "note": note}
    return (f"Proposal rule held: the sweep chose k {k}, p {p} and {was}. {note}",
            ("set_baseline_params", f"runs/{sid}", False, evidence))


def run_round(cfg: Config, planner) -> list[str]:
    records = ledger.read(cfg)
    state = state_from(records)
    rounds = state["rounds"]
    n = len(rounds)
    lines = [f"read: {n} finished round{'' if n == 1 else 's'} in the ledger" + (f"; last: {_short(rounds[-1])}" if n else "")]
    plan = planner.next(state)
    if plan is None:
        return lines + ["planner: rule (R6: nothing left on the list; waiting for a person or new trials)"]
    lines.append("planner: " + (f"model ({plan.host})" if plan.planner == "model" else
                               f"rule (model answer not used: {plan.fallback})" if plan.fallback else
                               f"rule ({plan.rule}: {plan.reason})"))
    lines.append(f"experiment: {plan.experiment} = {plan.tool} {json.dumps(plan.args, separators=(',', ':'))}")
    sid = ledger.begin_step(cfg, "round", GOAL, {
        "planner": plan.planner, "rule": plan.rule, "reason": plan.reason, "experiment": plan.experiment,
        "tool": plan.tool, "args": plan.args})
    lines.append(f"step {sid} started")
    call = {"tool": plan.tool, "args": plan.args}
    try:
        result, sha = tools.call(cfg, plan.tool, plan.args, cfg.home / "runs" / sid)
    except ToolError as e:
        conclusion = f"The tool failed: {e}. Nothing was proposed."
        ledger.add_step(cfg, sid, [{**call, "ok": False, "error": str(e)}], conclusion)
        return lines + [f"result: failed, {e}", f"conclusion: {conclusion}"]
    call.update(ok=True, result_sha256=sha, result=result)
    lines += [f"result: ok, sha256 {sha[:12]}", *("  " + x for x in _result_lines(plan.experiment, result))]
    proposal = None
    if plan.experiment in (E1, E3):
        conclusion = ("The reward round chose no policy, so nothing is proposed." if result["chosen_generation"] is None else
                      f"The reward round chose generation {result['chosen_generation']}. It is not checked on tuning "
                      "seeds 1 to 7 yet, so nothing is proposed.")
    elif plan.experiment in (E2, E4):
        conclusion, proposal = _judge_check(cfg, records, plan.experiment, plan.args, result)
    else:
        conclusion, proposal = _judge_sweep(cfg, result, sid)
    ledger.add_step(cfg, sid, [call], conclusion)
    if proposal:
        try:
            pid = ledger.propose(cfg, proposal[0], sid, *proposal[1:])
        except LedgerError as e:
            if "waiting for a person" not in str(e):
                raise
            conclusion += f" No new proposal: {e}."
        else:
            files = next(r for r in ledger.read(cfg) if r["type"] == "proposal" and r["id"] == pid)["files"]
            return lines + [f"conclusion: {conclusion}",
                            f"proposal {pid} pending: {proposal[0]}, {len(files)} file{'' if len(files) == 1 else 's'}. "
                            "A person signs off with: "
                            + APPROVE.format(id=pid)]
    return lines + [f"conclusion: {conclusion}"]
