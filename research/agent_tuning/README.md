# agent_tuning: making a small open model do only the agent's job

Optional. `research/agent/` runs with no model at all. This folder teaches a small open-weight
model the two narrow answers the agent can use, and measures it. The model card, with the choice
of model and the results, is `models/agent/card.md`.

The two tasks (one JSON object is the whole answer):

- Planner: the exact messages `research.agent.planner.ModelPlanner` sends; the answer is
  `{"tool", "args", "reason"}`, the rule planner's pick.
- Question: a fixed system line and a doctor's question; the answer is
  `{"template": ..., "threshold": ...}` or `{"refuse": true}`.

## Standard library (runs in RESEARCH-GATE)

- `tasks.py`: the prompts, the expected answers, and the guards (`guard_planner`,
  `guard_question`). The guards are the boundary: a model answer is acted on only if it passes.
- `generate.py`: writes the data. The rule planner and the question frames are the teacher.

      python -m research.agent_tuning.generate --out <dir>

  1,500 training and 400 held-out examples; same inputs, same bytes. A planner structure or a
  question frame is in one file only. Refusals: off-topic, change the product, approve or write,
  one patient, treatment advice, held-out or real data, prompt injection.
- `score.py`: the five metric groups, fixed before any model was run.

      python -m research.agent_tuning.score --data <heldout.jsonl> --outputs <outputs.jsonl> [--task T] [--per-group N]

## The optional environment (not needed by anything else)

Python 3.11 and the exact pins in `requirements.txt`. MindSpore is the backend: with MindNLP
imported, `transformers` and `peft` run on MindSpore ops. Both scripts stop if a real PyTorch
would be used instead.

    uv venv <env> --python 3.11
    uv pip install --python <env>/bin/python --only-binary :all: -r research/agent_tuning/requirements.txt

On Linux MindSpore also needs the system package `libgomp1`.

- `run_model.py`: a model's raw answers for a data file (greedy decoding; a stopped run resumes).

      <env>/bin/python research/agent_tuning/run_model.py --model <dir> [--adapter <dir>] --data <file> --out <file> [--task T] [--per-group N]

- `train_lora.py`: LoRA fine-tuning. `--data_url` and `--train_url` are the ModelArts
  training-job argument names, so the same file runs as a job there.

      <env>/bin/python research/agent_tuning/train_lora.py --data_url <dir with train.jsonl> --train_url <out dir> --model <dir> [--per-group N]

Weights, adapters and data stay outside the repository
(`~/Desktop/Projects/biosentry-data/models/` and `.../agent-tuning/`). The card records where the
weights came from and their sha256.

## Limits

- On a laptop CPU the MindSpore backend is slow: about 12 seconds per question example and about
  50 seconds per planner example in training, about 3 seconds per generated token. The card says
  what was run here and what is left for a ModelArts job.
- The guard's word lists were written with the held-out refusal frames in view, so "after the
  guards" on refusals is zero by construction. The model's own refusal accuracy is the honest
  number for new wording.
- A model that passes the guard can still pick the wrong template for a good question. That runs
  one of the three fixed experiments on simulated tuning trials, nothing else.
