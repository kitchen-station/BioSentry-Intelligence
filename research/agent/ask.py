"""The doctor's question. The text is matched to one of three fixed templates by keywords, and the
tool is `evaluate_rules` on tuning seeds. The only thing taken from the text is one integer, the
NEWS2 threshold. The answer is fixed sentences filled with numbers from the tool result."""

import re

from . import ledger, tools
from .config import Config
from .tools import ToolError

MAX_QUESTION = 500
LAST_LINE = "Simulated patients. Not validated. A person decides."
REFUSAL = (
    "I can run three things on the simulated ward, on tuning seeds 1 to 10 only: (1) alerting at a NEWS2 total "
    "you name, from 1 to 20, against the current NEWS2 chart trigger; (2) a patient's own baseline against the "
    "chart trigger; (3) how many alerts a nurse gets per 12-hour shift. I did not run anything for this question.\n"
    + LAST_LINE)
FIXED = ("NEWS2", "12-hour", "seeds 1 to 10", "per 100 patient-days", LAST_LINE)

_NEVER = re.compile(
    r"\b(approve|apply|ignore|disregard|override|overwrite|delete|deploy|install|adopt|write|edit|modify|"
    r"ledger|proposal|prompt|instructions?|p-[0-9]{4})\b|\b(change|set|update)\b.*\b(policy|model|models|file|setting|settings|params)\b"
    r"|held[- ]?out|real (patients?|data)|actual patients?|\bseeds?\s*(1[1-9]|[23][0-9])\b"
    r"|\b(this|that|my|our|one|single|named) patient\b|\bpatient\s+[a-z]?-?[0-9]+\b|\bbed\s+[0-9]+\b|\b(mr|mrs|ms|dr)\b"
    r"|\b(dose|dosage|treat|treatment|treating|medication|drug|prescribe|antibiotics?|mg)\b")
_THRESHOLD = re.compile(r"threshold|cut-?\s?off|score of|total of|news2? of|\bat \d|\d+ or (more|higher|above)|>=")
_NURSE = re.compile(r"\bnurses?\b")
_ALERT = re.compile(r"\b(alerts?|alarms?)\b")
_LOAD = re.compile(r"\b(shifts?|workload|how many)\b")


def _baseline(t: str) -> bool:
    return bool(re.search(r"baseline|usual values|their usual", t))


def match(question: str) -> tuple[str | None, int | None]:
    """(template, threshold). (None, None) is a refusal."""
    t = question[:MAX_QUESTION].lower()
    if _NEVER.search(t):
        return None, None
    base, thresh = _baseline(t), bool(_THRESHOLD.search(t))
    ints = {int(x) for x in re.findall(r"[0-9]+", re.sub(r"news2|12[- ]hour", "", t))}
    n = next(iter(ints)) if len(ints) == 1 and 1 <= min(ints) <= 20 else None
    if _NURSE.search(t) and _ALERT.search(t) and _LOAD.search(t):
        if base and thresh:
            return None, None
        if thresh:
            return ("alerts_per_nurse", n) if n else (None, None)
        return "alerts_per_nurse", None
    if thresh and not base:
        return ("news2_threshold", n) if n else (None, None)
    if base and not thresh:
        return "baseline_vs_news2", None
    return None, None


CHART = {"rule": "news2_chart_trigger"}


def _rules(template: str, threshold: int | None, question: str) -> list[dict]:
    ge = {"rule": "news2_total_ge", "threshold": threshold}
    if template == "news2_threshold":
        return [CHART, ge]
    if template == "baseline_vs_news2":
        return [CHART, {"rule": "baseline_alone"}, {"rule": "news2_or_baseline"}]
    if threshold:
        return [ge]
    return [{"rule": "news2_or_baseline"}] if _baseline(question.lower()) else [CHART]


def _s(v: object) -> str:
    return "n/a" if v is None else str(v)


def _caught(x: dict) -> str:
    return f"{x['caught'][0]} of {x['caught'][1]}"


def _chance(x: dict) -> str:
    return f"{x['chance'][0]} of {x['chance'][1]}"


def _change(x: dict) -> str:
    return _s(x["vs_first"]["extra_alerts_per_100_patient_days"])


def build(template: str, args: dict, r: dict) -> str:
    """Fixed sentences. Numbers are the result's values as they are: no arithmetic, no rounding."""
    first = f"On the simulated ward ({r['trials']} trials, tuning seeds 1 to 10, {r['episodes_not_cut_short']} deterioration episodes not cut short), "
    windows = "the same time windows on patients with no episode held an alert in "
    x, rule = r["rules"], args["rules"][-1]
    if template == "news2_threshold":
        a, b = x[1], x[0]
        threshold = rule["threshold"]
        body = [
            first + f"alerting at a NEWS2 total of {threshold} or more caught {_caught(a)} episodes early, before the "
            f"deterioration was fully in. The current NEWS2 chart trigger caught {_caught(b)}.",
            f"Chance control: {windows}{_chance(a)} windows at the new threshold and {_chance(b)} windows for the chart "
            "trigger. Read the catches next to these.",
            f"Alarms: {_s(a['alerts_per_100_patient_days'])} alerts per 100 patient-days at the new threshold, "
            f"{_s(b['alerts_per_100_patient_days'])} for the chart trigger (a change of {_change(a)}).",
        ]
    elif template == "baseline_vs_news2":
        b, a, c = x
        body = [
            first + f"the NEWS2 chart trigger caught {_caught(b)} episodes early, before the deterioration was fully in. "
            f"The patient's own baseline alone caught {_caught(a)}, and NEWS2 or the baseline together caught {_caught(c)}.",
            f"Chance control: {windows}{_chance(b)} windows for the chart trigger, {_chance(a)} for the baseline alone "
            f"and {_chance(c)} for NEWS2 or the baseline. Read the catches next to these.",
            f"Alarms: {_s(b['alerts_per_100_patient_days'])} alerts per 100 patient-days for the chart trigger, "
            f"{_s(a['alerts_per_100_patient_days'])} for the baseline alone (a change of {_change(a)}), "
            f"{_s(c['alerts_per_100_patient_days'])} for NEWS2 or the baseline (a change of {_change(c)}).",
        ]
    else:
        (a,) = x
        what = {"news2_total_ge": f"alerting at a NEWS2 total of {rule.get('threshold')} or more",
                "news2_or_baseline": "NEWS2 or the patient's own baseline",
                "news2_chart_trigger": "the current NEWS2 chart trigger"}[rule["rule"]]
        if r["staff_nurses"] is None:
            alarms = ("Alarms: the trials do not give staffing, so this is alerts per patient per 12-hour shift "
                      f"instead: {_s(a['alerts_per_patient_12h'])}.")
        else:
            alarms = (f"Alarms: {_s(a['alerts_per_nurse_per_shift'])} alerts per nurse per 12-hour shift. The nurse "
                      f"number is staff nurses per shift from the trial's scenario ({r['staff_nurses']}), 12-hour "
                      "shifts, alerts spread evenly across the nurses.")
        body = [
            first + f"{what} caught {_caught(a)} episodes early, before the deterioration was fully in.",
            f"Chance control: {windows}{_chance(a)} windows. Read the catches next to this.",
            alarms,
        ]
    return "\n".join([*body, LAST_LINE])


def _numbers(obj: object, out: set) -> set:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k != "seeds":  # "seeds 1 to 10" is a fixed phrase; the list would ground any small number
                _numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _numbers(v, out)
    elif type(obj) in (int, float):
        out.add(str(obj))
    return out


def grounded(text: str, args: dict, result: dict) -> bool:
    """After the fixed phrases are removed, every number must be in this run's arguments or result."""
    for phrase in FIXED:
        text = text.replace(phrase, "")
    allowed = _numbers(result, _numbers(args, set()))
    return all(n in allowed for n in re.findall(r"-?[0-9]+(?:\.[0-9]+)?", text))


def ask(cfg: Config, question: str) -> str:
    template, threshold = match(question)
    args = None
    if template:
        args = {"dataset": "ward-v2", "seeds": list(range(1, 11)), "window": "early",
                "rules": _rules(template, threshold, question)}
    sid = ledger.begin_step(cfg, "ask", question, {
        "planner": "keywords", "rule": template or "none",
        "reason": "matched a fixed template" if template else "no template fits",
        "tool": "evaluate_rules" if template else None, "args": args})
    if not template:
        ledger.add_step(cfg, sid, [], "Refused: no template fits this question.")
        return REFUSAL
    try:
        result, sha = tools.call(cfg, "evaluate_rules", args)
    except ToolError as e:
        ledger.add_step(cfg, sid, [{"tool": "evaluate_rules", "args": args, "ok": False, "error": str(e)}],
                        f"The tool failed: {e}")
        raise
    call = {"tool": "evaluate_rules", "args": args, "ok": True, "result_sha256": sha, "result": result}
    answer = build(template, args, result)
    if grounded(answer, args, result):
        ledger.add_step(cfg, sid, [call], " ".join(answer.splitlines()), grounding="ok")  # one line: the ledger replaces newlines
        return answer
    ledger.add_step(cfg, sid, [call], "Refused: a number in the answer is not in the tool result.", grounding="failed")
    return REFUSAL
