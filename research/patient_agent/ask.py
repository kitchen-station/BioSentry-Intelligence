"""A clinician's question about ONE patient. A keyword matcher picks one of four fixed kinds; the answer is
fixed sentences filled from this patient's data only. The data is outside input: every type is checked, and every
text field of it is quoted and capped, never read as an instruction. Nothing is written or kept."""

import json
import re
import unicodedata
from datetime import UTC, datetime

from research.agent.ask import _ALWAYS, _ORDER, _odd_digit

MAX_QUESTION = 500
CAP = 300  # characters of one quoted outside string
MAX_NODES = 200_000
MAX_LIST = 2000
LAST = "Synthetic data. Not validated. Not advice on treatment."
FIRST = "For this patient only:"
NO_BASELINE = "The data for this patient holds no baseline at this time."
FIXED = ("NEWS2", "SpO2", "15-minute")
KINDS = ("own_baseline", "recent_change", "why_rank", "active_alerts")
VITALS = ("heart_rate", "respiratory_rate", "spo2", "temperature")
NAMES = {"heart_rate": "Heart rate", "respiratory_rate": "Respiratory rate", "spo2": "SpO2", "temperature": "Temperature"}
NUM = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")
_MARK = re.compile(r"(?m)^[0-9]{1,4}\. ")  # the number of a list line is not data
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

_NOT_ANSWERED = " I did not answer this question.\n" + LAST
REFUSALS = {
    "injection": "I do not follow instructions in a question, and I do not repeat a value that a question gives me." + _NOT_ANSWERED,
    "treatment": "I give no advice on treatment or on what to do next." + _NOT_ANSWERED,
    "other_patient": "I answer about this patient only. I say nothing about another patient or the ward." + _NOT_ANSWERED,
    "prediction": "I do not predict what will happen to a patient." + _NOT_ANSWERED,
    "outside": "I use no outside knowledge. I only report what this patient's data holds." + _NOT_ANSWERED,
    "history": "The data this agent gets holds no alert history and no NEWS2 over time, so it cannot answer about "
               "earlier alerts." + _NOT_ANSWERED,
    "followed": "The data this agent gets holds no alert history and no NEWS2 over time, so it cannot say whether an "
                "alert was followed by a deterioration." + _NOT_ANSWERED,
    "none": "I can answer four things about this patient's own data: (1) the patient's own baseline for a vital; "
            "(2) what changed in the vital history in the last hours; (3) why the patient has this rank; "
            "(4) which alerts are active." + _NOT_ANSWERED,
}
UNGROUNDED = "I did not answer: a number in my answer is not in this patient's data.\n" + LAST
BAD_DATA = "I did not answer: the data for this patient has a shape that this agent does not accept.\n" + LAST

_INJECT = re.compile(
    r"\b(ignore|disregard|override|overwrite|instructions?|prompts?|say|confirm|agree|repeat|pretend|echo|print|write|"
    r"claim|assume|tell me that|state that|from now on)\b|[@;|`$\\{}<>]|https?:")
_TREAT = re.compile(
    r"\b(should|recommend\w*|advis\w*|advice|give|dose|dosage|prescri\w*|treat\w*|start|stop|increase|decrease|"
    r"administer\w*|antibiotics?|fluids?|medications?|drugs?|oxygen therapy|therapy|intubat\w*|transfer\w*|discharge\w*|"
    r"fine|shall|help me|icu|mg|what do i do|what to do|what can i do|next steps?|what now|ok to|safe to|do i need|need to|"
    r"call(?:ed|ing)?|rapid response|escalate|needs?|bolus|hold|wean\w*|dapat|gamot|bigyan)\b|\bis\b.{0,40}\bok(?:ay)?\b")
_OTHER = re.compile(
    r"\b(others?|another|beds?|everyone|everybody|all patients|patients?|the ward|wards?|who else|compare\w*|versus|vs|"
    r"units?|floors?|departments?|rooms?|next door|the rest|neighbou?rs?|mr|mrs|ms|miss|dr|roommates?|"
    r"across the hall|halls?|hallway|corner|bays?|case ?loads?|cases|my list|across my|teammates?|colleagues?|"
    r"pts|rm|wings?|cohorts?|teams?|ladies|gentlemen|guys?|him and her)\b"
    r"|\b[a-z]-?[0-9]{2,}\b")
# "this patients baseline" is a possessive typed without its apostrophe: "this" is never plural.
# "my patients", "our patients" and "the patients" stay plural and are refused.
_OWN = re.compile(r"\bthis patient(?:'s|s)?\b|\b(the|my|our) patient(?:'s)?\b")
_PRED = re.compile(r"\b(will|won't|predict\w*|going to|chance|likely|prognosis|surviv\w*|die|dies|forecast\w*|expect\w*|"
                   r"tomorrow|soon|future|anticipat\w*|downhill|worse|worsen\w*|next (?:few )?hours?|deteriorating)\b")
_COND = (r"sepsis|septic|pneumonia|hypox\w*|tachy\w*|brady\w*|fever|pyrexia|shock|copd|infection|delirium|aki|diabetes|"
         r"hypertension|hypotension|stroke|heart failure|arrhythmia|anaemia|anemia")
_OUTSIDE = re.compile(rf"\b({_COND}|defin\w*|guidelines?|explain\w*|meaning|mean|means|normal range|reference range|"
                      r"healthy|typical\w*|in general|generally|textbook|usually|adults?|children|child|person|people|humans?)\b")
_FOLLOWED = re.compile(r"\b(alerts?|alarms?|news2|flag\w*)\b.{0,40}\b(follow\w*|led to|lead to|preced\w*|before|after)\b"
                       r".{0,40}\b(deteriorat\w*|worse\w*|collaps\w*|declin\w*)\b|\bdeteriorat\w*.{0,40}\b(alerts?|alarms?)\b")
_HIST = re.compile(
    r"\b(alerts?|alarms?)\b.{0,40}\b(yesterday|earlier|before|previous\w*|ever|so far|stay|history|since|past|ago|"
    r"overnight|today|all|last)\b|\b(history|earlier|previous|past|prior|all)\b.{0,25}\b(alerts?|alarms?)\b"
    r"|\bhow many (alerts?|alarms?)\b|\bhow (?:often|many times)\b.{0,40}\b(?:alert\w*|alarms?|fire[sd]?)\b|news2 (over time|history|trend)|\b(score|news2).{0,20}over time")
_KIND = {
    "own_baseline": re.compile(r"\bbaseline|\bnormal\b|\busual\b|\bown (values?|range)\b"),
    "recent_change": re.compile(r"\bchang\w*|\brecent\w*|\bhours?\b"),
    "why_rank": re.compile(r"\brank\w*|\bflagged\b|\bon the list\b|\bpriority\b|\bhigh on\b"),
    "active_alerts": re.compile(r"\balerts?\b|\balarms?\b|\bescalat\w*"),
}
PHRASES = {"own_baseline": "this patient's own baseline", "recent_change": "what changed in this patient's vital history",
           "why_rank": "why this patient has this rank", "active_alerts": "which alerts are active for this patient"}
_VITAL = {
    "heart_rate": re.compile(r"\bhr\b|heart[ -]?rate|\bpulse\b"),
    "respiratory_rate": re.compile(r"\brr\b|resp(?:iratory)?[ -]?rate|breathing rate|\brespiration\b"),
    "spo2": re.compile(r"\bspo2\b|oxygen saturation|\bsats?\b|\bsaturation\b"),
    "temperature": re.compile(r"\btemp\b|\btemperature\b"),
}
_NOT_LISTED = re.compile(r"blood pressure|\bbp\b|systolic|diastolic|consciousness|\bacvpu\b|\bavpu\b|\bgcs\b|glucose|"
                         r"\bweight\b|\burine\b")
_NAME = re.compile(r"\b[A-Z][a-z]+(?:'s)?\b")


def _plain(t: str) -> str:
    return re.sub(r"\bnews ?2\b|\bspo2\b|\bo2\b", " ", t)


def _norm(question: str) -> str:
    """The typographic quote marks that a phone types, as ASCII."""
    return question.translate({0x2019: "'", 0x2018: "'", 0x201C: '"', 0x201D: '"'})


def _refusal(question: object) -> str | None:
    """The key of the refusal list that fires first, or None."""
    if type(question) is not str or not 1 <= len(question) <= MAX_QUESTION or _odd_digit(question):
        return "none"
    question = _norm(question)
    if not question.isascii():  # a look-alike letter or a zero-width character can hide a word
        return "injection"
    t = re.sub(r"(?<=\b[a-z])[-.](?=[a-z]\b)", "", question.lower())  # "w-a-r-d" is "ward"
    if _INJECT.search(t) or _ALWAYS.search(t) or _ORDER.search(t):
        return "injection"
    if _TREAT.search(t):
        return "treatment"
    # a capital word after the first word is a name or a place: not this patient
    if _OTHER.search(_OWN.sub(" ", t)) or any(m.start() > 0 and not re.search(r"[.?!]\s+$", question[:m.start()])
                                                        for m in _NAME.finditer(question)):
        return "other_patient"
    if _FOLLOWED.search(t):
        return "followed"
    if _PRED.search(t):
        return "prediction"
    if _OUTSIDE.search(t):
        return "outside"
    if _HIST.search(t):
        return "history"
    return None


def _hours(plain: str) -> tuple[bool, int | None]:
    """(ok, the one whole number 1 to 72 written next to an hours word, or None when no number is written)."""
    tokens = re.findall(r"[0-9]+(?:[.,][0-9]+)*", plain)
    if not tokens:
        return True, None
    got = re.findall(r"(?<![\w.,])([1-9][0-9]?)\s*-?\s*(?:hours?|hrs|h)\b", plain)
    if len(tokens) == 1 and got == tokens and 1 <= int(got[0]) <= 72:
        return True, int(got[0])
    return False, None


def _named(t: str) -> list[str]:
    return [v for v in VITALS if _VITAL[v].search(t)]


def match(question: str) -> tuple[str | None, dict | str]:
    """(kind, args), or (None, the key of the fixed refusal)."""
    key = _refusal(question)
    if key:
        return None, key
    t = _norm(question).lower()
    kinds = [k for k, rx in _KIND.items() if rx.search(t)]
    if len(kinds) != 1:
        return None, "none"
    plain, kind, named = _plain(t), kinds[0], _named(t)
    if kind == "recent_change":
        ok, n = _hours(plain)
        return (kind, {"vital": named[0] if named else None, "hours": n or 6}) if ok and len(named) < 2 else (None, "none")
    if re.search(r"[0-9]", plain):  # a number in the question is never taken
        return None, "none"
    if kind == "own_baseline":
        return (kind, {"vital": named[0] if named else None}) if len(named) < 2 and not _NOT_LISTED.search(t) \
            else (None, "none")
    return kind, {}


def guard(output_text: str, question: str) -> tuple[str, dict] | None:
    """For a later model: its output must be exactly {"kind","vital","hours"} (or a refusal), and agree with the question."""
    if type(output_text) is not str or len(output_text) > 2000 or _refusal(question):
        return None
    try:
        obj = json.loads(output_text)
    except (ValueError, RecursionError):
        return None
    if type(obj) is not dict or set(obj) != {"kind", "vital", "hours"}:
        return None
    kind, vital, hours = obj["kind"], obj["vital"], obj["hours"]
    if type(kind) is not str or kind not in KINDS or not (vital is None or vital in VITALS) \
            or not (hours is None or (type(hours) is int and 1 <= hours <= 72)):
        return None
    t = _norm(question).lower()
    plain, named = _plain(t), _named(t)
    if kind in ("own_baseline", "recent_change") and (len(named) > 1 or vital != (named[0] if named else None)):
        return None
    if kind == "recent_change":
        ok, n = _hours(plain)
        if not ok or hours != n:
            return None
        picked = (kind, {"vital": vital, "hours": n or 6})
    else:
        if hours is not None or re.search(r"[0-9]", plain) or (kind != "own_baseline" and vital is not None):
            return None
        picked = (kind, {"vital": vital} if kind == "own_baseline" else {})
    got = match(question)
    return picked if got[0] is None or got == picked else None


# --- the data ------------------------------------------------------------------------------------------------------

class Bad(Exception):
    """The data has a shape this agent does not accept."""


def _num(x: object) -> str:
    """A numeric leaf as the answer writes it. One function: the answer and the check both use it."""
    if type(x) is int and abs(x) < 10**9:
        return str(x)
    if type(x) is float and abs(x) < 10**9:  # NaN fails this test too
        s = repr(int(x) if x.is_integer() else x)
        if "e" not in s:
            return s
    raise Bad


def _when(s: object) -> datetime:
    if type(s) is not str or len(s) > 40:
        raise Bad
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        raise Bad from None
    if d.tzinfo is None:
        raise Bad
    return d.astimezone(UTC)


def fmt_time(s: object) -> str:
    d = _when(s)
    return f"{d.day} {MONTHS[d.month - 1]} {d.year} {d.hour:02d}:{d.minute:02d} UTC"


_KEEP = "°≥≤±µ"


NOT_SHOWN = "(not shown: this text holds a character that this agent does not show)"


def _quote(x: object) -> str:
    """The inside of the quotes. A stored text is shown only when every character is printable ASCII, a tab or a
    line break, or one of a few unit signs. Any other character and the WHOLE text is replaced by NOT_SHOWN:
    cleaning it could change a number (drop a minus sign, join "38" and "5" across a dot sign). The only changes
    made to a shown text: `"` and the backtick become `'`, white space runs become one space, and a long text is
    cut at a space (never inside a number) at or before CAP characters."""
    if type(x) is not str:
        raise Bad
    if any(not ("\x20" <= c <= "\x7e" or c in _KEEP or c in "\t\n\r") for c in x):
        return NOT_SHOWN
    s = re.sub(r"[ \t\n\r]+", " ", x.replace('"', "'").replace("`", "'")).strip()
    if len(s) > CAP:
        cut = s.rfind(" ", 0, CAP + 1)
        s = (s[:cut] if cut > 0 else "") + "..."
    return s


def _tokens(text: str) -> list[str]:
    for phrase in FIXED:
        text = text.replace(phrase, " ")
    return NUM.findall(_MARK.sub("", text))


class _Out:
    """Writes the pieces that hold data, and keeps the number tokens it wrote."""

    def __init__(self):
        self.used: list[str] = []

    def n(self, x: object) -> str:
        v = _num(x)
        self.used.append(v)
        return v

    def t(self, x: object) -> str:
        v = fmt_time(x)
        self.used += _tokens(v)
        return v

    def q(self, x: object) -> str:
        body = _quote(x)
        self.used += _tokens(body)
        return f'  "{body}"'


def _dict(x: object) -> dict:
    if type(x) is not dict:
        raise Bad
    return x


def _list(x: object) -> list:
    if type(x) is not list or len(x) > MAX_LIST:
        raise Bad
    return x


def _word(x: object, allowed: tuple) -> str:
    if type(x) is not str or x not in allowed:
        raise Bad
    return x


def _int(x: object, low: int = -(10**9), high: int = 10**9) -> int:
    if type(x) is not int or not low <= x <= high:
        raise Bad
    return x


def _need(d: dict, key: str) -> object:
    """A field the kind reads. A missing or null one makes the data unusable."""
    if d.get(key) is None:
        raise Bad
    return d[key]


RULES = {"tier_a_threshold": "NEWS2 threshold", "tier_c_raise": "model raise", "tier_b_finding": "finding",
         "missing_input": "missing input"}
UNAVAILABLE = {"no_history": "the data holds no history for this vital.",
               "latest_bin_missing": "the latest 15-minute value is missing.",
               "patch_not_live": "the patch is not live."}


def _baseline(w: _Out, args: dict, base: object) -> list[str]:
    base = _dict(base)
    window = _int(_dict(_need(base, "params")).get("window_bins"))
    entries = _list(_need(base, "vitals"))
    out = []
    for v in ([args["vital"]] if args["vital"] else VITALS):
        e = next((_dict(x) for x in entries if _dict(x).get("vital") == v), None)
        name = NAMES[v]
        if e is None:
            out.append(f"{name}: the data holds no baseline entry.")
        elif e.get("status") == "forming":
            out.append(f"{name}: the baseline is still forming: {w.n(_int(e.get('bins_valid')))} of "
                       f"{w.n(_int(e.get('bins_needed')))} values.")
        elif e.get("status") == "unavailable":
            out.append(f"{name}: no baseline, because {UNAVAILABLE[_word(e.get('reason'), tuple(UNAVAILABLE))]}")
        elif e.get("status") == "computed":
            start = ("at admission" if _word(e.get("anchored_at"), ("admission", "first_kept_bin")) == "admission"
                     else "at the first value the system kept, not at admission")
            out.append(f"{name}: the baseline is {w.n(e.get('baseline_median'))}, with this unit:\n{w.q(e.get('unit'))}\n"
                       f"It is the median of the first {w.n(window)} valid 15-minute values. The window starts {start}.")
            out.append(f"The latest stored value is {w.n(e.get('latest'))} at {w.t(e.get('latest_at'))}. "
                       f"The difference from the baseline is {w.n(e.get('delta'))}. The z value is {w.n(e.get('z'))}.")
            if type(e.get("significant")) is not bool:
                raise Bad
            direction = e.get("direction")
            tail = "" if direction is None else (
                " The direction is " + _word(direction, ("rising", "falling", "stable", "new")) + ".")
            since = e.get("since")
            out.append(("The change is significant." if e["significant"] else "The change is not significant.") + tail
                       + ("" if since is None else f" The run started at {w.t(since)}."))
        else:
            raise Bad
    return out


def _changes(w: _Out, args: dict, detail: dict) -> list[str]:
    if args["vital"] == "temperature":
        return ["The vital history holds no temperature. It holds heart rate, respiratory rate and SpO2 only. "
                "It holds no blood pressure or consciousness history."]
    hist, hours = _dict(_need(detail, "vital_history")), args["hours"]
    out = []
    for v in ([args["vital"]] if args["vital"] else VITALS[:3]):
        bins, prev = [], None
        for b in _list(_need(hist, v)):
            b = _dict(b)
            when = _when(b.get("time"))
            if prev is not None and when <= prev:  # oldest first, one bin for each time
                raise Bad
            prev = when
            if b.get("value") is not None:
                _num(b["value"])
                bins.append((when, b["value"], b["time"]))
        name = NAMES[v]
        if not bins:
            out.append(f"{name}: the history holds no value.")
            continue
        end = bins[-1][0]
        win = [x for x in bins if (end - x[0]).total_seconds() <= hours * 3600]
        first, last = win[0], win[-1]
        low, high = min(win, key=lambda x: x[1]), max(win, key=lambda x: x[1])
        word = "It rose." if last[1] > first[1] else "It fell." if last[1] < first[1] else "It is the same."
        out.append(f"{name} in the last {w.n(hours)} hours: the first value is {w.n(first[1])} at {w.t(first[2])}. "
                   f"The latest value is {w.n(last[1])} at {w.t(last[2])}. {word} "
                   f"The lowest value is {w.n(low[1])} at {w.t(low[2])}. "
                   f"The highest value is {w.n(high[1])} at {w.t(high[2])}.")
    out.append("The window ends at the newest value of each vital, not at the time of the question. The history holds "
               "heart rate, respiratory rate and SpO2 only. It holds no temperature, blood pressure or consciousness history.")
    return out


def _reasons(w: _Out, detail: dict) -> list[str]:
    got = _list(_need(detail, "escalation_reasons"))
    if not got:
        return ["No alert reason is active for this patient now."]
    out = ["These alert reasons are active:"]
    for i, r in enumerate(got, 1):
        r = _dict(r)
        out.append(f"{i}. Rule: {RULES[_word(r.get('rule'), tuple(RULES))]}\n{w.q(r.get('text'))}")
        if r.get("pathway") is not None:
            out.append(f"   Pathway:\n{w.q(r['pathway'])}")
    return out


def _rank(w: _Out, detail: dict) -> list[str]:
    return [f"This patient is number {w.n(_int(_need(detail, 'rank'), 1))} on your list.",
            f"The NEWS2 total is {w.n(_int(_need(detail, 'score'), 0, 20))}.",
            f"The trend is {_word(_need(detail, 'trend'), ('rising', 'falling', 'stable', 'insufficient'))}.",
            "The system gives this supporting text:\n" + w.q(_need(detail, "supporting")), *_reasons(w, detail)]


def _shape(patient: object) -> tuple[dict, bool]:
    """(the one row that is set, whether it is the abstained row). Exactly one of detail and abstained is set."""
    p = _dict(patient)
    if type(p.get("format")) is not int or p["format"] != 1 or p.get("synthetic") is not True:
        raise Bad
    det, ab = p.get("detail"), p.get("abstained")
    if (det is None) == (ab is None):
        raise Bad
    return _dict(det if det is not None else ab), ab is not None


def build(kind: str, args: dict, patient: dict) -> tuple[str, list[str]]:
    """Fixed sentences and the outside strings, quoted. `used` is the number tokens written, in order."""
    data, abstained = _shape(patient)
    w = _Out()
    if abstained:
        lines = ["This patient is NOT RANKED, so this agent has no answer. The system gives this reason:", w.q(_need(data, "reason")),
                 "The system gives this instruction:", w.q(_need(data, "instruction"))]
        phrase = "that this patient is not ranked"
    else:
        phrase = PHRASES[kind]
        if kind == "own_baseline":
            # contract-patient-ask: `baseline` is null for a ranked patient when the baseline read gave nothing.
            lines = [NO_BASELINE] if "baseline" in patient and patient["baseline"] is None else _baseline(w, args, patient.get("baseline"))
        elif kind == "recent_change":
            lines = _changes(w, args, data)
        elif kind == "why_rank":
            lines = _rank(w, data)
        else:
            lines = [*_reasons(w, data), "The data holds no history of earlier alerts."]
    return "\n".join([FIRST, *lines, f"I answered only this: {phrase}. I used nothing else in the question.", LAST]), w.used


def _roots(kind: str, args: dict, patient: dict) -> list:
    """The fields of the data that the kind reads: the only ones a number of the answer may come from."""
    if patient.get("abstained") is not None:
        return [patient["abstained"]]
    d, v = patient["detail"], args.get("vital")
    if kind == "own_baseline":
        b = patient["baseline"]
        if b is None:
            return []
        return [b["params"]["window_bins"], *[e for e in b["vitals"] if v is None or e.get("vital") == v]]
    if kind == "recent_change":
        return [d["vital_history"][x] for x in ([v] if v else VITALS[:3]) if x != "temperature"]
    if kind == "why_rank":
        return [d["rank"], d["score"], d["supporting"], d["escalation_reasons"]]
    return [d["escalation_reasons"]]


def grounded(text: str, used: list[str], kind: str, args: dict, patient: dict) -> bool:
    """The number tokens of the text equal `used`, and each one is in a field that the kind reads (a numeric leaf as
    written, a token of a string the text quotes, a token of a time the text writes) or is the hours argument."""
    if _odd_digit(text) or _tokens(text) != used:
        return False
    allowed = {str(args["hours"])} if type(args) is dict and type(args.get("hours")) is int else set()
    stack, seen = _roots(kind, args, patient), 0
    while stack:
        x = stack.pop()
        seen += 1
        if seen > MAX_NODES:
            return False
        if type(x) is dict:
            stack += x.values()
        elif type(x) is list:
            stack += x
        elif type(x) is str:
            body = _quote(x)
            if f'  "{body}"' in text:
                if not set(_tokens(body)) <= set(NUM.findall(x)):  # the cleaning made a number the stored text lacks
                    return False
                allowed.update(_tokens(body))
            if len(x) <= 40:
                try:
                    allowed.update(_tokens(fmt_time(x)))
                except Bad:
                    pass
        elif type(x) in (int, float):
            try:
                allowed.add(_num(x))
            except Bad:
                pass
    return all(u in allowed for u in used)


def answer(patient: dict, question: str, route=None) -> dict:
    """status answered | refused | ungrounded | bad_data. `route(question)` gives a (kind, args) that the guard passed,
    or None. It is called only where no list refused the question and the matcher has no kind: never otherwise.
    `planner` says which decided: keywords or tuned-model."""
    kind, args = match(question)
    out = {"status": "refused", "text": REFUSALS.get(args, REFUSALS["none"]) if kind is None else None, "kind": None,
           "args": None, "numbers": None, "reason": args if kind is None else None, "planner": "keywords"}
    if kind is None:
        if route is None or args != "none":
            return out
        try:
            picked = route(question)
        except Exception:  # noqa: BLE001 - a router failure never fails the job
            picked = None
        if type(picked) is not tuple or len(picked) != 2 or picked[0] not in KINDS or type(picked[1]) is not dict:
            return out
        kind, args = picked
        out["planner"] = "tuned-model"
    try:
        text, used = build(kind, args, patient)
    except Exception:  # noqa: BLE001 - any odd data is a fixed refusal, never a crash
        return {**out, "status": "bad_data", "text": BAD_DATA, "kind": kind, "args": args, "reason": "bad_data"}
    out.update(kind=kind, args=args, numbers=used, reason=None)
    try:
        ok = grounded(text, used, kind, args, patient)
    except Exception:  # noqa: BLE001
        ok = False
    return {**out, "status": "answered", "text": text} if ok else {**out, "status": "ungrounded", "text": UNGROUNDED, "reason": "ungrounded"}
