"""Run a model over a data file and write its raw answers, one line per example.

    python research/agent_tuning/run_model.py --model <dir> [--adapter <dir>] --data <file.jsonl> \
        --out <outputs.jsonl> [--per-group N] [--task planner|question]

Greedy decoding. At most 200 new tokens for the planner task and 40 for the question task. A run
that stops can be started again: ids already in --out are skipped. Score with
`python -m research.agent_tuning.score`.
"""

import argparse
import json
import time
from pathlib import Path

from common import backend, load, read

MAX_NEW = {"planner": 200, "question": 40}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--adapter")
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--per-group", type=int)
    ap.add_argument("--task", choices=("planner", "question"))
    a = ap.parse_args()
    _, torch = backend()
    examples = read(a.data, a.per_group, a.task)
    done = set()
    if a.out.exists():
        done = {json.loads(line)["id"] for line in a.out.read_text().splitlines() if line}
    tok, model = load(a.model, torch, a.adapter)
    model.eval()
    with open(a.out, "a") as out:
        for i, ex in enumerate(examples, 1):
            if ex["id"] in done:
                continue
            text = tok.apply_chat_template(ex["messages"], tokenize=False, add_generation_prompt=True)
            ids = tok(text, return_tensors="pt")
            t = time.time()
            with torch.no_grad():
                got = model.generate(**ids, max_new_tokens=MAX_NEW[ex["task"]], do_sample=False)
            new = got[0][ids["input_ids"].shape[1]:]
            answer = tok.decode(new, skip_special_tokens=True)
            out.write(json.dumps({"id": ex["id"], "output": answer}) + "\n")
            out.flush()
            print(f"{i}/{len(examples)} {ex['id']} {len(new)} tokens {time.time() - t:.0f} s", flush=True)


if __name__ == "__main__":
    main()
