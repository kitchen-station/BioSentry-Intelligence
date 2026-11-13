"""LoRA fine-tuning of the chosen model on the generated pairs, on the MindSpore backend.

    python research/agent_tuning/train_lora.py --data_url <dir with train.jsonl> --train_url <out dir> \
        --model <dir> [--per-group N] [--epochs 1] [--lr 2e-4] [--max_len 1536] [--device CPU]

`--data_url` and `--train_url` are the ModelArts training-job argument names. Prepared for a
job there, not run as one (see README.md). LoRA r 8, alpha 16, dropout 0.05 on the attention query and value
projections. The loss is on the answer tokens only. Batch size 1. Fixed seed. The adapter is
saved to `<train_url>/adapter` every 50 steps and at the end; `log.jsonl` has the loss per step.
An example longer than --max_len stops the run before it starts (a cut example has no answer left
to learn from), and so does a loss that is not a number.
"""

import argparse
import json
import math
import sys
import time
from pathlib import Path

from common import backend, encode, load, read

SEED = 20261113
LORA = {"r": 8, "lora_alpha": 16, "lora_dropout": 0.05, "target_modules": ["q_proj", "v_proj"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data_url", required=True, type=Path)
    ap.add_argument("--train_url", required=True, type=Path)
    ap.add_argument("--model", required=True)
    ap.add_argument("--per-group", type=int)
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--max_len", type=int, default=1536)
    ap.add_argument("--device", default="CPU")
    a = ap.parse_args()
    mindspore, torch = backend(a.device)
    mindspore.set_seed(SEED)
    torch.manual_seed(SEED)
    from peft import LoraConfig, get_peft_model

    examples = read(a.data_url / "train.jsonl", a.per_group)
    tok, model = load(a.model, torch)
    encoded = [encode(tok, ex) for ex in examples]
    longest = max(len(ids) for ids, _ in encoded)
    if longest > a.max_len:
        sys.exit(f"refused: an example is {longest} tokens, over --max_len {a.max_len}")
    model.train()
    model = get_peft_model(model, LoraConfig(task_type="CAUSAL_LM", **LORA))
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=a.lr)
    a.train_url.mkdir(parents=True, exist_ok=True)
    (a.train_url / "run.json").write_text(json.dumps({
        "examples": len(examples), "per_group": a.per_group, "epochs": a.epochs, "lr": a.lr, "max_len": a.max_len,
        "longest_example_tokens": longest, "seed": SEED, "lora": LORA, "trainable_parameters": sum(p.numel() for p in params),
        "mindspore": mindspore.__version__, "ids": [ex["id"] for ex in examples]}, indent=1) + "\n")
    step = 0
    with open(a.train_url / "log.jsonl", "a") as log:
        for epoch in range(a.epochs):
            for ex, (ids, labels) in zip(examples, encoded):
                ids, labels = torch.tensor([ids], dtype=torch.int64), torch.tensor([labels], dtype=torch.int64)
                t = time.time()
                loss = model(input_ids=ids, labels=labels).loss
                if not math.isfinite(float(loss)):
                    sys.exit(f"refused: the loss is not a number at step {step + 1} ({ex['id']}); nothing more was saved")
                loss.backward()
                opt.step()
                for p in params:  # optimizer.zero_grad() needs a profiler mindtorch does not have
                    p.grad = None
                step += 1
                rec = {"step": step, "epoch": epoch, "id": ex["id"], "tokens": int(ids.shape[1]),
                       "loss": round(float(loss), 5), "seconds": round(time.time() - t, 1)}
                log.write(json.dumps(rec) + "\n")
                log.flush()
                print(rec, flush=True)
                if step % 50 == 0:
                    model.save_pretrained(str(a.train_url / "adapter"))
    model.save_pretrained(str(a.train_url / "adapter"))
    print("saved", a.train_url / "adapter")


if __name__ == "__main__":
    main()
