"""The two tuning tasks: the prompts, the expected answers and the guards.

The model's answer is data. A guard decides whether it is acted on, so the weights only choose.
"""

import json
import re

from research.agent import ask, planner

TEMPLATES = ("news2_threshold", "baseline_vs_news2", "alerts_per_nurse")
REFUSE = '{"refuse": true}'
QUESTION_SYSTEM = (
    "You route a clinician's question about alerting rules on a simulated hospital ward to one fixed template. "
    'Answer with one JSON object and nothing else: {"template": "news2_threshold" (alert at a NEWS2 total) | '
    '"baseline_vs_news2" (a patient\'s own baseline against the NEWS2 chart trigger) | "alerts_per_nurse" '
    '(alerts per nurse per 12-hour shift), "threshold": <the whole number 1 to 20 written in the question, '
    'or null>} or {"refuse": true}. Refuse everything else.')

# Added on top of ask's own lists. The verbs refuse an order to change something, not a what-if question.
_EXTRA = re.compile(
    r"\b(change|set|update|raise|lower|increase|decrease|reduce|switch|enable|disable|adjust|replace|make|put|"
    r"turn|move|bump|tweak|alter|amend|push)\s+(the|our|it|this|that|all|every|to|up|down|off|on)\b")
_WHAT_IF = re.compile(r"\b(what if|what happens|what would|would|how (would|many|much|does)|if we|if the|suppose|compare)\b")
_ALWAYS = re.compile(r"\b(go ahead|sign off|activate|admin|developer mode|jailbreak|you are now|pretend|forget|new task)\b|system:")
_TOPIC = re.compile(r"news ?2|nurs\w*|alerts?\b|alerting|alarms?|baseline|threshold|cut-?\s?off|trigger|"
                    r"deteriorat\w*|early warning|usual values|their usual")


def planner_example(state: dict) -> dict:
    opens = planner.open_experiments(state)
    plan = planner.RulePlanner().next(state)
    expected = json.dumps({"tool": plan.tool, "args": plan.args, "reason": plan.rule}, separators=(",", ":"))
    return {"task": "planner", "messages": planner.messages(state, opens), "expected": expected,
            "label": {"tool": plan.tool, "args": plan.args}}


def question_example(question: str, template: str | None, threshold: int | None = None) -> dict:
    label = {"refuse": True} if template is None else {"template": template, "threshold": threshold}
    return {"task": "question",
            "messages": [{"role": "system", "content": QUESTION_SYSTEM}, {"role": "user", "content": question}],
            "expected": REFUSE if template is None else json.dumps(label), "label": label, "question": question}


def opens_of(example: dict) -> list[planner.Plan]:
    """The open experiments the example's digest shows, rebuilt from its rounds."""
    return planner.open_experiments({"rounds": json.loads(example["messages"][1]["content"])["rounds"]})


def guard_planner(text: str, opens: list[planner.Plan], key: str = "") -> planner.Plan | None:
    """The plan when the answer passes the agent's own check, else None (fall back to the rule planner)."""
    raw = json.dumps({"choices": [{"message": {"content": text}}]}).encode()
    try:
        # an empty key would match every text in _holds_key, so use a placeholder no answer contains
        return planner._pick(raw, opens, key or "\0no key\0")
    except planner._Reject:
        return None


def question_shape(text: str) -> dict | None:
    """The parsed answer when it is exactly one of the allowed shapes, else None."""
    try:
        o = json.loads(text.strip())
    except (ValueError, RecursionError):
        return None
    if type(o) is not dict:
        return None
    if list(o) == ["refuse"] and o["refuse"] is True:
        return o
    t = o.get("threshold")
    if set(o) == {"template", "threshold"} and o["template"] in TEMPLATES and (
            t is None or (type(t) is int and 1 <= t <= 20)):
        return o
    return None


def refuses(question: str) -> bool:
    """True when the guard's own checks refuse the question, whatever the model says."""
    low = question.lower()
    got = ask.read(question)
    # baseline and threshold words together fit no template
    return (got is None or bool(got[1] and got[2]) or bool(_ALWAYS.search(low)) or not _TOPIC.search(low)
            or bool(_EXTRA.search(low) and not _WHAT_IF.search(low)))


def guard_question(text: str, question: str) -> tuple[str | None, int | None]:
    """(template, threshold) to run, or (None, None) for the fixed refusal."""
    if refuses(question):
        return None, None
    o = question_shape(text)
    if o is None or "refuse" in o:
        return None, None
    template, threshold = o["template"], o["threshold"]
    # the number must be the one in the question; baseline_vs_news2 takes none, news2_threshold needs one
    if threshold != ask.read(question)[3] or (template == "news2_threshold" and threshold is None) \
            or (template == "baseline_vs_news2" and threshold is not None):
        return None, None
    matched = ask.match(question)  # where the keyword matcher answers, it wins
    if matched != (None, None) and matched != (template, threshold):
        return None, None
    return template, threshold
