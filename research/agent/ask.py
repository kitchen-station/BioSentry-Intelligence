"""The doctor's question. The text is matched to one of three fixed templates by keywords, and the
tool is `evaluate_rules` on tuning seeds. The only thing taken from the text is one integer, the
NEWS2 threshold. The answer is fixed sentences filled with numbers from the tool result."""

import re

from biosentry.domain.scoring.tier_a.tables import MEDIUM_FROM

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
    r"ledger|proposal|prompt|instructions?|p-[0-9]{4}|commit\w*|authori[sz]\w*|lock in|for everyone|from now on|"
    r"from tomorrow|permanent(ly)?|in the product|go(es)? live|going live|roll(ed)? out|live system|on all wards|"
    r"for the ward)\b|\b(change|set|update)\b.*\b(policy|model|models|file|setting|settings|params)\b"
    r"|held[- ]?out|hold-?out|real[- ]world|real (patients?|data)|\bactual\b|\bproduction\b|\bdefault\b"
    r"|\b(validation|test) (data|set|seeds?)\b|\bseeds?\s*(1[1-9]|[23][0-9])\b"
    r"|\b(this|that|my|our|one|single|named) patient\b|\bpatient\s+(number|no|id|named?|called)\b|\bpatient\s+[a-z]?-?[0-9]+\b"
    r"|\bbed\s+[0-9]+\b|\broom\b|\bward\s+[0-9]+\b|\b(mr|mrs|ms|dr|he|she|him|her|his|hers|bay|gentleman|lady|man|woman|boy|girl|child)\b"
    r"|\b(last (night|week|month|year)|yesterday|today|tonight)\b"
    r"|\b(dose|dosage|treat|treatment|treating|medication|drug|prescribe|antibiotics?|mg|oxygen|fluids?|icu|intubat\w*|transfer|discharge)\b"
    r"|@|https?:|\b(e-?mail|send|upload|download|execute|sudo|curl|rm)\b|[;|`$\\]")
_THRESHOLD = re.compile(r"threshold|cut-?\s?off|score of|total of|\bat\s+[0-9]|reach(es)?\s+[0-9]|[0-9]+ or (more|higher|above)|>=|≥")
# "above 5" could mean 5 or 6, and "under 7" is the opposite rule: refused, never guessed
_STRICT = re.compile(r"(above|over|more than|greater than|higher than|exceeds?|exceeding|less than|fewer than|lower than|"
                     r"under|below|not|>(?!=)|<)\s*[-+]?[0-9]")
_NUMBER = re.compile(r"[-+]?[0-9]+(?:[.,][0-9]+)?[a-z%]*")  # a sign, a decimal or glued letters make it unusable
_NURSE = re.compile(r"\bnurses?\b")
_ALERT = re.compile(r"\b(alert(s|ed)?|alarms?)\b")
_LOAD = re.compile(r"\b(shifts?|workload|burden|how many)\b")


_OLD = str(MEDIUM_FROM)  # the current total trigger: the only old value a comparison may name
_FILL = r"(?:\s+(?:a|the|current|usual|up|down|threshold|cut-?\s?off|total|score|of))*"
# which number is the new one must be said: "7 instead of 5", "from 5 to 7", "to 7 from 5"
_VERSUS = (re.compile(rf"\b([0-9]+){_FILL}\s+(?:instead of|rather than|in place of){_FILL}\s+{_OLD}\b"),
           re.compile(rf"\bfrom{_FILL}\s+{_OLD}{_FILL}\s+to{_FILL}\s+([0-9]+)\b"),
           re.compile(rf"\bto{_FILL}\s+([0-9]+)\s+from{_FILL}\s+{_OLD}\b"))


def _new_value(plain: str, numbers: list[str]) -> int | None:
    """The new threshold of a comparison with the current one, when exactly those two numbers are there."""
    for rx in _VERSUS:
        m = rx.search(plain)
        if m and re.fullmatch(r"[1-9][0-9]?", m[1]) and sorted(numbers) == sorted([m[1], _OLD]) \
                and 1 <= int(m[1]) <= 20 and m[1] != _OLD:
            return int(m[1])
    return None


def _baseline(t: str) -> bool:
    return bool(re.search(r"baseline|usual values|their usual", t))


def _odd_digit(text: str) -> bool:
    """A digit that is not 0 to 9 (another script, a fraction, a superscript)."""
    return any(c.isnumeric() and c not in "0123456789" for c in text)


# An order to change something is refused; a what-if question about the same change runs.
_ORDER = re.compile(
    r"\b(change|set|update|raise|lower|increase|decrease|reduce|switch|enable|disable|adjust|replace|make|put|"
    r"turn|move|bump|tweak|alter|amend|push)\s+(the|our|it|this|that|all|every|to|up|down|off|on)\b")
_WHAT_IF = re.compile(r"\b(what if|what happens|what would|how would|suppose|if we)\b")
_ALWAYS = re.compile(r"\b(go ahead|sign off|activate|admin|developer mode|jailbreak|you are now|pretend|forget|new task|"
                     r"going forward|rollout|sav(e|ed|ing)|keep it|implement\w*|config\w*)\b|system:")


def read(question: str) -> tuple[str, bool, bool, int | None] | None:
    """(lower-case text, baseline words, threshold words, the one usable whole number 1 to 20 or None).
    None when the refusal list, the order rule, the digit rules or the number rules refuse the question."""
    t = question[:MAX_QUESTION].lower()
    if _NEVER.search(t) or _ALWAYS.search(t) or _odd_digit(t) or (_ORDER.search(t) and not _WHAT_IF.search(t)):
        return None
    plain = re.sub(r"news ?2|12[- ]hour", " ", t)
    base, thresh = _baseline(t), bool(_THRESHOLD.search(plain))
    numbers = _NUMBER.findall(plain)
    n = int(numbers[0]) if len(numbers) == 1 and re.fullmatch(r"[1-9][0-9]?", numbers[0]) else None
    if n is not None and not 1 <= n <= 20:
        n = None
    if len(numbers) == 2:
        n = _new_value(plain, numbers)
    # A number is only ever the threshold: one whole number from 1 to 20, next to a threshold word.
    if numbers and (n is None or not thresh or _STRICT.search(plain)):
        return None
    return t, base, thresh, n


def match(question: str) -> tuple[str | None, int | None]:
    """(template, threshold). (None, None) is a refusal."""
    got = read(question)
    if got is None:
        return None, None
    t, base, thresh, n = got
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
    before = "by the time the deterioration was fully in"
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
    return answer(cfg, question)["text"]


def answer(cfg: Config, question: str, route=None) -> dict:
    """What `ask` does, with the record: status answered | no_template | ungrounded, the text, the ledger step id,
    the planner (keywords | tuned-model), and the template, args, result, result_sha256 and numbers (None when no
    tool ran). `route(question)` gives a (template, threshold) the guard passed, or None: then the matcher decides."""
    picked = route(question) if route else None
    planner, why = ("tuned-model", "the tuned model's answer passed the guard") if picked else ("keywords", None)
    template, threshold = picked or match(question)
    args = None
    if template:
        args = {"dataset": "ward-v2", "seeds": list(range(1, 11)), "window": "early",
                "rules": _rules(template, threshold, question)}
    sid = ledger.begin_step(cfg, "ask", question, {
        "planner": planner, "rule": template or "none",
        "reason": why or ("matched a fixed template" if template else "no template fits"),
        "tool": "evaluate_rules" if template else None, "args": args})
    out = {"status": "no_template", "text": REFUSAL, "step": sid, "planner": planner, "template": None, "args": None,
           "result": None, "result_sha256": None, "numbers": None}
    if not template:
        ledger.add_step(cfg, sid, [], "Refused: no template fits this question.")
        return out
    try:
        result, sha = tools.call(cfg, "evaluate_rules", args)
    except ToolError as e:
        ledger.add_step(cfg, sid, [{"tool": "evaluate_rules", "args": args, "ok": False, "error": str(e)}],
                        f"The tool failed: {e}")
        raise
    call = {"tool": "evaluate_rules", "args": args, "ok": True, "result_sha256": sha, "result": result}
    text, used = build(template, args, result)
    out.update(template=template, args=args, result=result, result_sha256=sha, numbers=used)
    if grounded(text, used, args, result):
        ledger.add_step(cfg, sid, [call], " ".join(text.splitlines()), grounding="ok")  # one line: the ledger replaces newlines
        return {**out, "status": "answered", "text": text}
    ledger.add_step(cfg, sid, [call], "Refused: a number in the answer is not in the tool result.", grounding="failed")
    return {**out, "status": "ungrounded"}
