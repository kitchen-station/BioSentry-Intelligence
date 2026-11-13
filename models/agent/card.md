# The research agent's language model (optional; research only)

| | |
|---|---|
| id | `agent` |
| what it is for | one narrow job for `research/agent/`: pick the next experiment as one JSON tool call, or route a doctor's question to one of three fixed experiments or refuse |
| status | optional and not wired in. `research/agent/` runs with no model at all (rule planner, keyword matcher). Nothing here touches the API, the rank or the arbiter. The tuned adapter is not loaded by any product code |
| model that was tuned | `Qwen/Qwen2.5-0.5B-Instruct`, Apache-2.0, 0.49 billion parameters. Fallback `Qwen/Qwen3-0.6B`, Apache-2.0, not downloaded |
| hosted target | `deepseek-v4-flash` on Huawei Cloud MaaS (CN-Hong Kong). Open weights, MIT. Never called: there is no Huawei Cloud account yet |
| framework | MindSpore 2.7.0 as the backend, through MindNLP (pinned as `mindnlp==0.5.1`; the installed library reports itself as 0.5.0rc2). No other framework trained anything |
| data | generated from our own rule planner and question frames. No patient data of any kind |
| code | `research/agent_tuning/` |
| full record | `training/agent/REGISTRY.md` (every model considered, weights, adapter, data, sources). The run log and the notebook come in the next pull request, with the held-out numbers |

## What stops the model doing anything else

The weights do not. Fine-tuning makes the model better at the one job; it cannot make it unable to
do other things. The boundary is the code that reads every answer:

- Planner: the answer must parse as exactly `{"tool", "args", "reason"}`, pass the tool's schema,
  and equal one of the experiments that are open at that point. Anything else is dropped and the
  rule planner decides (`research/agent/planner.py`, `_pick`).
- Question: the refusal lists run on the question first. The answer must be exactly one of two
  shapes. A threshold must be the one whole number from 1 to 20 that is written in the question.
  Where the keyword matcher answers, the model must agree with it. Anything else is the fixed
  refusal (`research/agent_tuning/tasks.py`, `guard_question`).
- Numbers in an answer to a doctor are checked against the tool result (`research/agent/ask.py`,
  `grounded`). The model never writes that answer.
- No model output can propose, approve or apply a change. Those are code rules and typed commands.

Read every figure below with that in mind: a wrong model answer costs a fall-back or a refusal,
not a wrong action.

## What was run here

All of it on one laptop (Apple M4, CPU only, shared with other jobs). Nothing ran in any cloud.

- The base weights were downloaded from the publisher's repository and checked by sha256
  (registry, section 5).
- The first full run (476 steps) learned nothing. On this backend `loss.backward()` is an empty
  function, so the adapter never changed. This was found by opening the adapter file, before any
  held-out answer existed. That run is kept as a frozen-model control.
- The script now takes gradients with `mindtorch.autograd.value_and_grad` and writes Adam out from
  basic operators (the library's own Adam needs an operator with no CPU kernel). The written-out
  Adam was checked against AdamW on a test problem: equal to 3e-16. The script refuses to go on if
  the adapter has not moved after the first step.
- The second run was stopped by plan after step 200 of 476 (159 question examples and 41 planner
  examples, 127 minutes). The stop was fixed for time before any held-out answer existed. It was
  not chosen on a score.
- Proof of learning: the loss was below the frozen control on 199 of the 199 steps after the
  first, on the same examples in the same order. Mean question loss in steps 151 to 200: 0.060
  trained, 0.499 frozen.

## Results on the blind held-out subsample

**Held-out evaluation running. No before/after numbers yet.** No tuned or untuned model score
exists in this repository. Do not read the training loss above as a result: it shows the adapter
learned the training examples, not that it answers new questions.

What is fixed already, with no model involved (`python -m research.agent_tuning.score --baseline ...`):

| On the blind held-out questions | All 300 | Subsample of 78 (the one the models are run on) |
|---|---|---|
| Keyword matcher: answerable questions right | 73 of 180 | 14 of 30 |
| Keyword matcher: refusal questions refused | 116 of 120 | 46 of 48 |
| Perfect answers, after the guard: answerable questions acted on | 81 of 180 | 20 of 30 |

The matcher is the thing a model has to beat. The last row is the most any model can get through
the guard. The metrics were fixed before any model was run: valid JSON, exact tool and arguments,
refusals, and the count of answers outside the schema (target zero). The subsample (the first 6
questions of each group, and the first 3 planner examples of each group: 78 and 9) was also fixed
beforehand.

## What is prepared for Huawei Cloud, and not run

- `train_lora.py` takes the ModelArts training-job argument names (`--data_url`, `--train_url`)
  and `--device` picks the MindSpore target. No job was run. A job still needs a MindSpore image
  for its hardware (the pins here are the CPU build) and the two folders mounted.
- The agent's model planner speaks the OpenAI-compatible API that MaaS documents. It is off unless
  three environment variables are set. The call to `deepseek-v4-flash` has never been made.
- Serving a tuned small model on Huawei Cloud was not found in any page that could be opened
  (registry, sections 10 and 11).

## Limits

- The evaluation, when it finishes, is small: 78 questions and 9 planner examples, one run, greedy
  decoding, no error bars. Its counts will be a first look, not a measurement.
- Training saw 200 of the 476 planned examples, once. No learning rate or length was tuned.
- The guard refuses about half of the held-out answerable questions whatever the model says: on
  all 180 a perfect model would get 81 through. The guard was frozen before any model answer and
  was not loosened afterwards.
- The refusal lists alone, with no model, stop 116 of the 120 held-out refusal questions. The other
  4 are stopped only if the model refuses them.
- Five phrases in the refusal list ("from tomorrow", "on all wards", "live system", "bay", "man")
  also appear in held-out refusal frames, because the same example sentences reached both. Those
  frames are not an independent test of the list.
- 22 training questions were dropped as near copies of a held-out question. So the held-out text
  decided which training questions were kept; nothing else of it reached training.
- The planner task is close to copying: the label is always the first open experiment in the
  message, and about three in four examples show only one.
- An order worded with a what-if phrase ("what if we set it to 7 and make it stick") can still
  run a simulation. Nothing is changed by any question.
- The keyword matcher sends 10 held-out "alarm load per nurse at N" questions to the wrong
  experiment. It was frozen before the model runs and is left as found.
- Synthetic data only. Nothing here is clinical evidence.
