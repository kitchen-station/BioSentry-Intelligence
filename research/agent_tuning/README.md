# agent_tuning: making a small open model do only the agent's job

Optional. `research/agent/` runs with no model at all. This folder teaches a small open-weight
model the two narrow answers the agent can use, and measures it. The model card is `models/agent/card.md`; the
choice of model and every source are in `training/agent/REGISTRY.md`. The held-out scores are in
the card and in `training/agent/runs/run1.json`. After the guard, the tuned model alone is worse than the keyword
matcher on the blind questions (7 of 30 answerable questions right, against 13).

The two tasks (one JSON object is the whole answer):

- Planner: the exact messages `research.agent.planner.ModelPlanner` sends; the answer is
  `{"tool", "args", "reason"}`, the rule planner's pick. This task is close to trivial: the label is
  always the first open experiment in the message, and three in four examples have only one.
- Question: a fixed system line and a doctor's question; the answer is
  `{"template": ..., "threshold": ...}` or `{"refuse": true}`.

## Standard library (runs in RESEARCH-GATE)

- `tasks.py`: the prompts, the expected answers, and the guards (`guard_planner`,
  `guard_question`). The guards are the boundary: a model answer is acted on only if it passes.
- `generate.py`: writes the data. The rule planner and the question frames are the teacher.

      python -m research.agent_tuning.generate --out <dir>

  1,500 training and 400 held-out examples; same inputs, same bytes. A planner structure or a
  question frame is in one file only. Refusals: off-topic, change the product, approve or write,
  one patient, treatment advice, held-out or real data, prompt injection, and a threshold that
  cannot be told (two values, a range). The held-out question frames are `heldout_frames.json`.
  They were written by someone who had not seen the matcher or the guard, and are never edited to
  pass a check. A training question whose words overlap a held-out question by 0.8 or more is
  dropped (22 were), so the held-out text does decide which training questions are kept.
- `score.py`: the metric groups, fixed before any model was run.

      python -m research.agent_tuning.score --data <heldout.jsonl> --outputs <outputs.jsonl> [--task T] [--per-group N]
      python -m research.agent_tuning.score --data <heldout.jsonl> --baseline matcher|perfect [--task T] [--per-group N]

  `--baseline matcher` scores the keyword matcher `research.agent.ask.match`, the thing a model has
  to beat. `--baseline perfect` scores the labels themselves: what is left after the guard is the
  most any model could get.

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
  training-job argument names and `--device` picks the MindSpore target. It is prepared for a
  ModelArts job, not run as one: a job still needs a MindSpore image for its hardware (these pins
  are the CPU build), local or mounted paths for the two folders, and this folder as its start
  folder.

      <env>/bin/python research/agent_tuning/train_lora.py --data_url <dir with train.jsonl> --train_url <out dir> --model <dir> [--per-group N]

Weights, adapters and data stay outside the repository
(`~/Desktop/Projects/biosentry-data/models/` and `.../agent-tuning/`). The card records where the
weights came from and their sha256.

## Limits

- On a laptop CPU the MindSpore backend is slow: in the run that was kept, on a shared laptop, a
  question step took 22 seconds on average and a planner step 99 seconds; a generated token takes
  about 3 seconds. The card says
  what was run here and what is left for a ModelArts job.
- The guard refuses about half of the held-out answerable questions whatever the model says
  (they lack a word it requires, or use one it refuses), and where the keyword matcher answers,
  the matcher wins. So the most any model can get after the guard is well under 100%; the
  `perfect` baseline prints it.
- Five phrases in the refusal list ("from tomorrow", "on all wards", "live system", "bay", "man")
  also appear in held-out refusal frames and in no training question. They came from the same
  example sentences that were given to the frame writer, so they are not independent; they are
  the only thing stopping three held-out questions.
- An order worded as a question can still run a simulation when it has a what-if phrase ("what
  if we set it to 7 and make it stick"). Nothing is changed by any question; only a simulated
  experiment runs.
- A model that passes the guard can still pick the wrong template for a good question. That runs
  one of the three fixed experiments on simulated tuning trials, nothing else.

## Storage and the server

- `weights.json`: the sha256 of every base-model file and adapter file. The question worker's model mode checks
  the local copies against it at start.
- `upload_adapter.py`: `prepare` makes a release folder outside the repository (the adapter, its config with the
  base model's public name, a sha256 file and a card). `upload` sends that folder to a private Hugging Face model
  repository. It takes no token: it uses the login the owner makes on the machine. It uploads nothing unless the
  repository reports itself private. `upload` was never run.
- `SERVER.md`: what the team server needs to run the tuned model, with the measured memory and time.
