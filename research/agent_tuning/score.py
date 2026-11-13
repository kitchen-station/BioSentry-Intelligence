"""Scores a model's outputs on a data file. `python -m research.agent_tuning.score --data heldout.jsonl --outputs out.jsonl`.

An outputs line is {"id": ..., "output": "..."}; a missing id counts as an empty output.
`--per-group N` and `--task` cut the data file to the same subsample `run_model.py` ran (the first
N examples of each group in file order), so a reduced run is scored on exactly what it answered.
Five groups, per task and together: valid JSON, exact accuracy, refusal accuracy, outside the allowed
shapes, and what is left after the guards. `--baseline perfect` scores the labels themselves, which shows the ceiling the guards leave. `--baseline matcher` scores the keyword matcher itself
on the questions, with no outputs file.
"""

import argparse
import json
from pathlib import Path

from research.agent import ask, tools
from research.agent_tuning import tasks
from research.agent_tuning.common import complete_lines, read

GROUPS = ("planner", "question", "both")
_KEYS = ("n", "valid_json", "exact_of", "exact", "outside_schema",
         "acted", "acted_other_open", "fell_back",  # planner
         "negatives", "refused_ok", "positives", "wrongly_refused",  # question
         "answerable_acted_right", "answerable_acted_wrong", "answerable_guard_refused",
         "refusal_acted", "refusal_guard_refused",
         "matcher_answers", "matcher_answers_exact", "matcher_silent", "matcher_silent_exact")


def _json_object(output: str) -> bool:
    try:
        return type(json.loads(output.strip())) is dict
    except (ValueError, RecursionError):
        return False


def _planner(ex: dict, output: str, m: dict) -> None:
    label = ex["label"]
    m["exact_of"] += 1
    plan = tasks.guard_planner(output, tasks.opens_of(ex))
    m["outside_schema"] += plan is None
    m["fell_back"] += plan is None
    if plan is None:
        return
    m["acted"] += 1
    right = (plan.tool, plan.args) == (label["tool"], label["args"])
    m["exact"] += right
    m["acted_other_open"] += not right


def _question(ex: dict, output: str, m: dict) -> None:
    label, shape = ex["label"], tasks.question_shape(output)
    negative = "refuse" in label
    m["outside_schema"] += shape is None
    template, threshold = tasks.guard_question(output, ex["question"])
    if negative:
        m["negatives"] += 1
        m["refused_ok"] += shape is not None and "refuse" in shape
        m["refusal_acted"] += template is not None
        m["refusal_guard_refused"] += template is None
        return
    m["positives"] += 1
    m["exact_of"] += 1
    exact = shape is not None and shape == label
    m["exact"] += exact
    m["wrongly_refused"] += shape is not None and "refuse" in shape
    matcher = ex["matcher"] if "matcher" in ex else ask.match(ex["question"])
    key = "matcher_answers" if matcher and matcher != (None, None) else "matcher_silent"
    m[key] += 1
    m[key + "_exact"] += exact
    if template is None:
        m["answerable_guard_refused"] += 1
    elif (template, threshold) == (label["template"], label["threshold"]):
        m["answerable_acted_right"] += 1
    else:
        m["answerable_acted_wrong"] += 1


def score(examples: list[dict], outputs: dict[str, str]) -> dict:
    out = {g: dict.fromkeys(_KEYS, 0) for g in GROUPS}
    for ex in examples:
        output = outputs.get(ex["id"], "")
        m = out[ex["task"]]
        m["n"] += 1
        m["valid_json"] += _json_object(output)
        (_planner if ex["task"] == "planner" else _question)(ex, output, m)
    for k in _KEYS:
        out["both"][k] = out["planner"][k] + out["question"][k]
    return out


def matcher_outputs(examples: list[dict]) -> dict[str, str]:
    """What the keyword matcher says for each question, in the model's answer format."""
    out = {}
    for ex in examples:
        template, threshold = ask.match(ex["question"])
        out[ex["id"]] = tasks.REFUSE if template is None else json.dumps({"template": template, "threshold": threshold})
    return out


def _pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100 * a / b:.1f}%)" if b else "0/0 (n/a)"


def render(result: dict) -> str:
    lines = []
    for g in GROUPS:
        m = result[g]
        lines += [f"{g}: {m['n']} examples",
                  f"  1 valid JSON object:        {_pct(m['valid_json'], m['n'])}",
                  f"  2 exact accuracy:           {_pct(m['exact'], m['exact_of'])}"]
        if g != "planner":
            lines += [f"     answerable, keyword matcher answers: {_pct(m['matcher_answers_exact'], m['matcher_answers'])}",
                      f"     answerable, keyword matcher silent:  {_pct(m['matcher_silent_exact'], m['matcher_silent'])}",
                      f"  3 refusal accuracy:         {_pct(m['refused_ok'], m['negatives'])}; "
                      f"answerable questions refused by the model {_pct(m['wrongly_refused'], m['positives'])}"]
        lines.append(f"  4 outside allowed shapes:   {m['outside_schema']} (target 0)")
        if g != "question":
            lines.append(f"  5 planner after the guard:  acted on {m['acted']}, of which on another open experiment "
                         f"(safe, not the rule's pick) {m['acted_other_open']}; fell back to the rule planner {m['fell_back']}")
        if g != "planner":
            lines += [f"  5 questions after the guard: answerable acted on, right template {m['answerable_acted_right']}, "
                      f"wrong template {m['answerable_acted_wrong']}; answerable refused by the guard "
                      f"{m['answerable_guard_refused']}",
                      f"     refusal questions acted on {m['refusal_acted']} (target 0); "
                      f"refusal questions refused {m['refusal_guard_refused']}"]
    return "\n".join(lines)


def _outputs(path: Path) -> dict[str, str]:
    rows = (json.loads(line) for line in complete_lines(path))
    return {row["id"]: str(row["output"]) for row in rows}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--outputs", type=Path)
    ap.add_argument("--baseline", choices=("matcher", "perfect"))
    ap.add_argument("--json", type=Path)
    ap.add_argument("--per-group", type=int)
    ap.add_argument("--task", choices=("planner", "question"))
    a = ap.parse_args()
    if bool(a.outputs) == bool(a.baseline):
        ap.error("give exactly one of --outputs and --baseline")
    examples = read(a.data, a.per_group, "question" if a.baseline == "matcher" else a.task)
    if a.baseline == "perfect":  # every answer is the label: what is left after the guards is the ceiling
        outputs = {e["id"]: e["expected"] for e in examples}
    else:
        outputs = matcher_outputs(examples) if a.baseline else _outputs(a.outputs)
    result = score(examples, outputs)
    print(render(result))
    if a.json:
        a.json.write_text(json.dumps(result, indent=1) + "\n")


if __name__ == "__main__":
    main()
