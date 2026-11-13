"""LoRA fine-tuning of the chosen model on the generated pairs, on the MindSpore backend.

    python research/agent_tuning/train_lora.py --data_url <dir with train.jsonl> --train_url <out dir> \
        --model <dir> [--per-group N] [--epochs 1] [--lr 2e-4] [--max_len 1536] [--device CPU]

`--data_url` and `--train_url` are the ModelArts training-job argument names. Prepared for a
job there, not run as one (see README.md). LoRA r 8, alpha 16, dropout 0.05 on the attention query and value
projections. The loss is on the answer tokens only. Batch size 1. Fixed seed. The adapter is
saved to `<train_url>/adapter` every 50 steps and at the end; `log.jsonl` has the loss per step.
An example longer than --max_len stops the run before it starts (a cut example has no answer left
to learn from), and so does a loss that is not a number.

Two things are done MindNLP's way, because the PyTorch-style calls do nothing here:
- gradients come from `autograd.value_and_grad(forward, params)`. On this backend
  `loss.backward()` is an empty function: it raises nothing and computes nothing;
- the Adam update is written out below. MindNLP 0.5.1's AdamW needs an operator (addcdiv) that
  has no CPU kernel.
The run stops if the adapter has not changed after the first step, so a run can never again
finish without having learned anything.
"""

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from common import backend, encode, load, read

SEED = 20261113
LORA = {"r": 8, "lora_alpha": 16, "lora_dropout": 0.05, "target_modules": ["q_proj", "v_proj"]}
ADAM = {"betas": [0.9, 0.999], "eps": 1e-8, "weight_decay": 0.01}


class Adam:
    """Adam with decoupled weight decay, from basic operators."""

    def __init__(self, torch, params, lr: float):
        self.torch, self.params, self.lr, self.t = torch, params, lr, 0
        self.m = [torch.zeros_like(p) for p in params]
        self.v = [torch.zeros_like(p) for p in params]

    def step(self) -> None:
        torch, (b1, b2) = self.torch, ADAM["betas"]
        self.t += 1
        with torch.no_grad():
            for i, p in enumerate(self.params):
                g = p.grad
                if g is None:
                    continue
                self.m[i] = b1 * self.m[i] + (1 - b1) * g
                self.v[i] = b2 * self.v[i] + (1 - b2) * g * g
                m_hat = self.m[i] / (1 - b1 ** self.t)
                v_hat = self.v[i] / (1 - b2 ** self.t)
                p.copy_(p - self.lr * (m_hat / (torch.sqrt(v_hat) + ADAM["eps"]) + ADAM["weight_decay"] * p))
                p.grad = None


def moved(named: list) -> float:
    """The mean size of the adapter's B weights. They start at exactly zero, so zero means nothing was learned."""
    b = [p for n, p in named if "lora_B" in n]
    return sum(float(abs(p).mean()) for p in b) / len(b)


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
    started, t0 = datetime.now(timezone.utc).isoformat(timespec="seconds"), time.time()
    mindspore, torch = backend(a.device)
    mindspore.set_seed(SEED)
    torch.manual_seed(SEED)
    import mindnlp
    import peft
    import tokenizers
    import transformers
    from peft import LoraConfig, get_peft_model

    examples = read(a.data_url / "train.jsonl", a.per_group)
    tok, model = load(a.model, torch)
    encoded = [encode(tok, ex) for ex in examples]
    longest = max(len(ids) for ids, _ in encoded)
    if longest > a.max_len:
        sys.exit(f"refused: an example is {longest} tokens, over --max_len {a.max_len}")
    model.train()
    model = get_peft_model(model, LoraConfig(task_type="CAUSAL_LM", **LORA))
    named = [(n, p) for n, p in model.named_parameters() if p.requires_grad]
    params = tuple(p for _, p in named)
    opt = Adam(torch, params, a.lr)
    from torch import autograd

    grad_fn = autograd.value_and_grad(lambda ids, labels: model(input_ids=ids, labels=labels).loss, params)
    a.train_url.mkdir(parents=True, exist_ok=True)
    (a.train_url / "run.json").write_text(json.dumps({
        "examples": len(examples), "per_group": a.per_group, "epochs": a.epochs, "lr": a.lr, "max_len": a.max_len,
        "longest_example_tokens": longest, "seed": SEED, "lora": LORA, "optimizer": {"name": "Adam, decoupled weight decay, written out", **ADAM},
        "gradients": "mindtorch.autograd.value_and_grad", "trainable_parameters": sum(p.numel() for p in params),
        "mindspore": mindspore.__version__, "started_at": started,
        **{m.__name__: getattr(m, "__version__", "unknown") for m in (mindnlp, transformers, peft, tokenizers)}, "ids": [ex["id"] for ex in examples]}, indent=1) + "\n")
    step = 0
    with open(a.train_url / "log.jsonl", "a") as log:
        for epoch in range(a.epochs):
            for ex, (ids, labels) in zip(examples, encoded):
                ids, labels = torch.tensor([ids], dtype=torch.int64), torch.tensor([labels], dtype=torch.int64)
                t = time.time()
                loss = grad_fn(ids, labels)  # also leaves each parameter's gradient on it
                if not math.isfinite(float(loss)):
                    sys.exit(f"refused: the loss is not a number at step {step + 1} ({ex['id']}); nothing more was saved")
                opt.step()
                step += 1
                if step == 1 and moved(named) == 0.0:
                    sys.exit("refused: the adapter did not change after the first step, so nothing is being learned")
                rec = {"step": step, "epoch": epoch, "id": ex["id"], "tokens": int(ids.shape[1]),
                       "loss": round(float(loss), 5), "seconds": round(time.time() - t, 1)}
                log.write(json.dumps(rec) + "\n")
                log.flush()
                print(rec, flush=True)
                if step % 50 == 0:
                    model.save_pretrained(str(a.train_url / "adapter"))
    model.save_pretrained(str(a.train_url / "adapter"))
    (a.train_url / "run-end.json").write_text(json.dumps({
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "wall_seconds": round(time.time() - t0, 1),
        "steps": step, "adapter_b_mean_abs": moved(named)}) + "\n")
    print("saved", a.train_url / "adapter")


if __name__ == "__main__":
    main()
