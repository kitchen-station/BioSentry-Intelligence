# training/agent: the record of the agent's fine-tuning run

Nothing here trains a model. It records a run that was made elsewhere (see
`research/agent_tuning/README.md`) and shows it. The data is generated and the ward is simulated.

- `runs/<run-id>.json`: one small file per run. Settings, hashes, the loss of every step, the
  before and after scores on the blind held-out subsample, and every example with its guard decision.
  Written by `research/agent_tuning/runlog.py`. Weights, adapters and data stay outside the repository.
- `REGISTRY.md`: every model considered, the weights, the adapter, the data and every source.
- `runs/run1.scores.json`: the before and after counts for the evaluation site, written by `--scores-out`.
  `python -m research.agent export --scores` reads it.
- `notebooks/finetune.ipynb`: reads only `runs/<run-id>.json` (set `RUN` in its second cell) and draws
  the tables and the loss curve with the standard library. `notebooks/execute.py` runs it again in place.

The first full run learned nothing: on this backend `loss.backward()` does nothing, so the adapter never
changed. Its log is kept as a frozen-model control, and the run log shows the trained loss next to it
and the adapter weights, so learning is measured. The stop at step 200 of 476 was fixed beforehand for
time and was not chosen on a score.

## Commands

Build the run log (needs the backend package, so run it in the test image; the data and run folders
are mounted read-only; `--eval` takes a folder or a path prefix for `<name>-question.jsonl` and
`<name>-planner.jsonl`):

    python -m research.agent_tuning.runlog --run-id <id> --train_url <run dir> --data_url <data dir> \
        --model <weights dir> --model-name Qwen/Qwen2.5-0.5B-Instruct --model-revision <sha> \
        --model-licence Apache-2.0 --eval untuned=<eval dir> --eval tuned=<eval dir> \
        --question-per-group 6 --planner-per-group 3 --hardware "<text>" --commit <sha> \
        --out training/agent/runs/<id>.json

Add `--scores-out <file>` to write the before and after counts for `python -m research.agent export --scores`.
It runs each distinct experiment once on the tuning seeds, so it needs the trials, like `ask`.

For the stopped run add `--train_url /run1 --log /run1/log-to-step200.jsonl --adapter /run1/adapter-step200
--control-log /run0/log.jsonl --stopped "<reason>"`, with the first full run's folder
(`run0-learned-nothing`) mounted read-only at `/run0`. Each answers file needs the `.meta.json` that
`run_model.py` writes beside it; the log is refused (exit 2) when its adapter or data hash does not match.

Run the notebook again (the optional environment's Python has nbformat, nbclient and ipykernel):

    <env>/bin/python training/agent/notebooks/execute.py

Test (runs without Jupyter):

    python -m pytest -q research/tests/test_agent_runlog.py research/tests/test_agent_training_notebook.py
