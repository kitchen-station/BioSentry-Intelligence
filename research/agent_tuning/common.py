"""Shared by run_model.py and train_lora.py. Needs the optional environment (requirements.txt).

MindSpore is the backend: `import mindnlp` makes `transformers` and `peft` run on MindSpore ops
(its `torch` module is MindNLP's mindtorch). The scripts stop if a real PyTorch would be used
instead, so a run can never change framework without anyone noticing.
"""

import json
import sys
from pathlib import Path


def backend(device: str = "CPU"):
    import mindspore

    mindspore.set_context(device_target=device)
    import mindnlp  # noqa: F401  (the import does the patching)
    import torch

    if "mindtorch" not in (getattr(torch, "__file__", "") or ""):
        sys.exit("refused: `torch` is not MindNLP's mindtorch, so this would not run on MindSpore")
    return mindspore, torch


def load(model_dir: str, torch, adapter: str | None = None):
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_dir)
    # float32: the CPU Gather kernel has no bfloat16
    model = AutoModelForCausalLM.from_pretrained(model_dir, dtype=torch.float32)
    if adapter:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, adapter)
    return tok, model


def encode(tok, ex: dict) -> tuple[list[int], list[int]]:
    """Prompt and answer as one sequence; the prompt positions are masked out of the loss."""
    prompt = tok.apply_chat_template(ex["messages"], tokenize=False, add_generation_prompt=True)
    p = tok(prompt)["input_ids"]
    a = tok(ex["expected"] + "<|im_end|>")["input_ids"]
    return p + a, [-100] * len(p) + a


def complete_lines(path: Path) -> list[str]:
    """The non-empty lines of a file a run may have stopped in the middle of: a last line that is not JSON is dropped."""
    rows = [line for line in path.read_text().splitlines() if line]
    if rows:
        try:
            json.loads(rows[-1])
        except ValueError:
            rows.pop()
    return rows


def group(example: dict) -> str:
    """The stratum an example is sampled from: the template or refusal category, or the planner's tool."""
    return "planner/" + example["label"]["tool"] if example["task"] == "planner" else "question/" + example["category"]


def read(path: Path, per_group: int | None, task: str | None = None) -> list[dict]:
    """The file's examples in file order; with per_group, the first N of each group; with task, that task only."""
    seen: dict[str, int] = {}
    out = []
    with open(path) as f:
        for line in f:
            ex = json.loads(line)
            if task and ex["task"] != task:
                continue
            g = group(ex)
            seen[g] = seen.get(g, 0) + 1
            if per_group is None or seen[g] <= per_group:
                out.append(ex)
    return out
