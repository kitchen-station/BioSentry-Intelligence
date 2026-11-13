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
    r"|held[- ]?out|hold-?out|real[- ]world|real (patients?|data)|actual patients?|\bseeds?\s*(1[1-9]|[23][0-9])\b"
    r"|\b(this|that|my|our|one|single|named) patient\b|\bpatient\s+(number|no|id|named?|called)\b|\bpatient\s+[a-z]?-?[0-9]+\b"
    r"|\bbed\s+[0-9]+\b|\broom\b|\bward\s+[0-9]+\b|\b(mr|mrs|ms|dr|he|she|him|her|his|hers)\b"
    r"|\b(last (night|week|month|year)|yesterday|today|tonight)\b"
    r"|\b(dose|dosage|treat|treatment|treating|medication|drug|prescribe|antibiotics?|mg|oxygen|fluids?|icu|intubat\w*|transfer|discharge)\b"
    r"|@|https?:|\b(e-?mail|send|upload|download|execute|sudo|curl|rm)\b|[;|`$\\]")
_THRESHOLD = re.compile(r"threshold|cut-?\s?off|score of|total of|\bat [0-9]|[0-9]+ or (more|higher|above)|>=|≥")
# "above 5" could mean 5 or 6: refused, never guessed
_STRICT = re.compile(r"(above|over|more than|greater than|higher than|exceeds?|exceeding|>(?!=))\s*-?[0-9]")
_NUMBER = re.compile(r"-?[0-9]+(?:[.,][0-9]+)?")
_NURSE = re.compile(r"\bnurses?\b")
_ALERT = re.compile(r"\b(alert(s|ed)?|alarms?)\b")
_LOAD = re.compile(r"\b(shifts?|workload|burden|how many)\b")


def _baseline(t: str) -> bool:
    return bool(re.search(r"baseline|usual values|their usual", t))


def _odd_digit(text: str) -> bool:
    """A digit that is not 0 to 9 (another script, a fraction, a superscript)."""
    return any(c.isnumeric() and c not in "0123456789" for c in text)


def match(question: str) -> tuple[str | None, int | None]:
    """(template, threshold). (None, None) is a refusal."""
    t = question[:MAX_QUESTION].lower()
    if _NEVER.search(t) or _odd_digit(t):
        return None, None
    plain = re.sub(r"news ?2|12[- ]hour", " ", t)
    base, thresh = _baseline(t), bool(_THRESHOLD.search(plain))
    numbers = _NUMBER.findall(plain)
    n = int(numbers[0]) if len(numbers) == 1 and re.fullmatch(r"[1-9][0-9]?", numbers[0]) else None
    if n is not None and not 1 <= n <= 20:
        n = None
    # A number is only ever the threshold: one whole number from 1 to 20, next to a threshold word.
    if numbers and (n is None or not thresh or _STRICT.search(plain)):
        return None, None
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


COUNTS = "An alert that was already on when the episode began counts."


def build(template: str, args: dict, r: dict) -> tuple[str, list[str]]:
    """Fixed sentences, and the numbers put into them, in order. Each number is a value of the
    result or the arguments as it is: no arithmetic, no rounding."""
    used: list[str] = []

    def n(value: object) -> str:
        if value is None:
            return "n/a"
        used.append(str(value))
        return used[-1]

    def of(pair: list) -> str:
        return f"{n(pair[0])} of {n(pair[1])}"

    x, rule = r["rules"], args["rules"][-1]
    first = (f"On the simulated ward ({n(r['trials'])} trials, tuning seeds 1 to 10, {n(r['episodes_not_cut_short'])} "
             "deterioration episodes with a complete window), ")
    before = "before the deterioration was fully in"
    windows = "the same time windows on patients with no episode held an alert in "
    if template == "news2_threshold":
        a, b = x[1], x[0]
        body = [
            first + f"a rule that alerts at a NEWS2 total of {n(rule['threshold'])} or more had an alert on for "
            f"{of(a['caught'])} episodes {before}. The current NEWS2 chart trigger had one on for {of(b['caught'])}. {COUNTS}",
            f"Chance control: {windows}{of(a['chance'])} windows at the new threshold and {of(b['chance'])} windows for "
            "the chart trigger. Read the catches next to these.",
            f"Alarms: {n(a['alerts_per_100_patient_days'])} alerts per 100 patient-days at the new threshold, "
            f"{n(b['alerts_per_100_patient_days'])} for the chart trigger (a change of "
            f"{n(a['vs_first']['extra_alerts_per_100_patient_days'])}).",
        ]
    elif template == "baseline_vs_news2":
        b, a, c = x
        body = [
            first + f"the NEWS2 chart trigger had an alert on for {of(b['caught'])} episodes {before}. The patient's own "
            f"baseline alone had one on for {of(a['caught'])}, and NEWS2 or the baseline together for {of(c['caught'])}. {COUNTS}",
            f"Chance control: {windows}{of(b['chance'])} windows for the chart trigger, {of(a['chance'])} for the baseline "
            f"alone and {of(c['chance'])} for NEWS2 or the baseline. Read the catches next to these.",
            f"Alarms: {n(b['alerts_per_100_patient_days'])} alerts per 100 patient-days for the chart trigger, "
            f"{n(a['alerts_per_100_patient_days'])} for the baseline alone (a change of "
            f"{n(a['vs_first']['extra_alerts_per_100_patient_days'])}), {n(c['alerts_per_100_patient_days'])} for NEWS2 or "
            f"the baseline (a change of {n(c['vs_first']['extra_alerts_per_100_patient_days'])}).",
        ]
    else:
        (a,) = x
        if rule["rule"] == "news2_total_ge":
            what = f"a rule that alerts at a NEWS2 total of {n(rule['threshold'])} or more"
        else:
            what = {"news2_or_baseline": "NEWS2 or the patient's own baseline",
                    "news2_chart_trigger": "the current NEWS2 chart trigger"}[rule["rule"]]
        body = [first + f"{what} had an alert on for {of(a['caught'])} episodes {before}. {COUNTS}",
                f"Chance control: {windows}{of(a['chance'])} windows. Read the catches next to this."]
        if r["staff_nurses"] is None:
            body.append("Alarms: the trials do not give staffing, so this is alerts per patient per 12-hour shift "
                        f"instead: {n(a['alerts_per_patient_12h'])}.")
        else:
            body.append(f"Alarms: {n(a['alerts_per_nurse_per_shift'])} alerts per nurse per 12-hour shift. The nurse "
                        f"number is staff nurses per shift from the trial's scenario ({n(r['staff_nurses'])}), 12-hour "
                        "shifts, alerts spread evenly across the nurses.")
    return "\n".join([*body, LAST_LINE]), used


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


def grounded(text: str, used: list[str], args: dict, result: dict) -> bool:
    """With the fixed phrases removed, the numbers in the text must be exactly the numbers that
    were put in, in the same order, and each of those must be in this run's arguments or result."""
    if _odd_digit(text):
        return False
    for phrase in FIXED:
        text = text.replace(phrase, " ")
    allowed = _numbers(result, _numbers(args, set()))
    return re.findall(r"-?[0-9]+(?:\.[0-9]+)?", text) == used and all(u in allowed for u in used)


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
    answer, used = build(template, args, result)
    if grounded(answer, used, args, result):
        ledger.add_step(cfg, sid, [call], " ".join(answer.splitlines()), grounding="ok")  # one line: the ledger replaces newlines
        return answer
    ledger.add_step(cfg, sid, [call], "Refused: a number in the answer is not in the tool result.", grounding="failed")
    return REFUSAL
