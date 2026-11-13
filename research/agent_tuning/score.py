"""Scores a model's outputs on a data file. `python -m research.agent_tuning.score --data heldout.jsonl --outputs out.jsonl`.

An outputs line is {"id": ..., "output": "..."}; a missing id counts as an empty output.
`--per-group N` and `--task` cut the data file to the same subsample `run_model.py` ran (the first
N examples of each group in file order), so a reduced run is scored on exactly what it answered.
Five groups, per task and together: valid JSON, exact accuracy, refusal accuracy, outside the allowed
shapes, and what is left after the guards.
"""

import argparse
import json
from pathlib import Path

from research.agent import tools
from research.agent_tuning import tasks
from research.agent_tuning.common import read

GROUPS = ("planner", "question", "both")
_KEYS = ("n", "valid_json", "exact_of", "exact", "negatives", "refused_ok", "positives", "wrongly_refused",
         "outside_schema", "acted", "acted_wrong", "fell_back")


def _json_object(output: str) -> bool:
    try:
        return type(json.loads(output.strip())) is dict
    except (ValueError, RecursionError):
        return False


def _planner(ex: dict, output: str, m: dict) -> None:
    label = ex["label"]
    m["exact_of"] += 1
    try:
        answer = json.loads(output.strip())
        args = tools.validate(answer["tool"], answer["args"])
        m["exact"] += (answer["tool"], args) == (label["tool"], label["args"])
    except (ValueError, RecursionError, KeyError, TypeError, tools.ToolError):
        pass
    plan = tasks.guard_planner(output, tasks.opens_of(ex))
    m["outside_schema"] += plan is None
    m["fell_back"] += plan is None
    if plan is not None:
        m["acted"] += 1
        m["acted_wrong"] += (plan.tool, plan.args) != (label["tool"], label["args"])


def _question(ex: dict, output: str, m: dict) -> None:
    label, shape = ex["label"], tasks.question_shape(output)
    negative = "refuse" in label
    m["outside_schema"] += shape is None
    if negative:
        m["negatives"] += 1
        m["refused_ok"] += shape is not None and "refuse" in shape
    else:
        m["positives"] += 1
        m["exact_of"] += 1
        m["exact"] += shape is not None and shape == label
        m["wrongly_refused"] += shape is not None and "refuse" in shape
    template, threshold = tasks.guard_question(output, ex["question"])
    if template is None:
        m["fell_back"] += 1
        return
    m["acted"] += 1
    m["acted_wrong"] += negative or (template, threshold) != (label["template"], label["threshold"])


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


def _pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100 * a / b:.1f}%)" if b else "0/0 (n/a)"


def render(result: dict) -> str:
    lines = []
    for g in GROUPS:
        m = result[g]
        lines += [f"{g}: {m['n']} examples",
                  f"  1 valid JSON object:        {_pct(m['valid_json'], m['n'])}",
                  f"  2 exact accuracy:           {_pct(m['exact'], m['exact_of'])}",
                  f"  3 refusal accuracy:         {_pct(m['refused_ok'], m['negatives'])}; "
                  f"positives wrongly refused {_pct(m['wrongly_refused'], m['positives'])}",
                  f"  4 outside allowed shapes:   {m['outside_schema']} (target 0)",
                  f"  5 after the guards:         acted on {m['acted']}, of which wrong {m['acted_wrong']}; "
                  f"fell back or refused {m['fell_back']}"]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--outputs", required=True, type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--per-group", type=int)
    ap.add_argument("--task", choices=("planner", "question"))
    a = ap.parse_args()
    examples = read(a.data, a.per_group, a.task)
    outputs = {}
    for line in a.outputs.read_text().splitlines():
        if line:
            row = json.loads(line)
            outputs[row["id"]] = row["output"]
    result = score(examples, outputs)
    print(render(result))
    if a.json:
        a.json.write_text(json.dumps(result, indent=1) + "\n")


if __name__ == "__main__":
    main()
