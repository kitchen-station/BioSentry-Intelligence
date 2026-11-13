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
| full record | `training/agent/REGISTRY.md` (every model considered, weights, adapter, data, sources), `training/agent/runs/run1.json` (the run log, with every held-out answer), `training/agent/notebooks/finetune.ipynb` (the loss curve and the tables) |

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

**After the guard, the tuned model alone is worse than the keyword matcher on the blind
questions: 7 of 30 answerable questions right, against 13.** It is better than the untuned model
(2 of 30). Neither model had a refusal question acted on. One run each, greedy decoding, no error
bars. Every count below is in `training/agent/runs/run1.json`, with every answer.

Questions: 78, of which 30 are answerable and 48 must be refused.

| | untuned | tuned (step 200) | keyword matcher | perfect answers (ceiling) |
|---|---|---|---|---|
| Valid JSON | 53 of 78 | 78 of 78 | 78 of 78 | 78 of 78 |
| Answers outside the two allowed shapes (target 0) | 73 | 0 | 0 | 0 |
| Answerable: the answer itself is exactly right | 2 of 30 | 13 of 30 | 14 of 30 | 30 of 30 |
| Answerable, after the guard: right experiment | 2 | 7 | 13 | 20 |
| Answerable, after the guard: wrong experiment | 0 | 0 | 2 | 0 |
| Answerable, after the guard: refused | 28 | 23 | 15 | 10 |
| Refusal questions the answer itself refuses | 0 of 48 | 48 of 48 | 46 of 48 | 48 of 48 |
| Refusal questions acted on after the guard (target 0) | 0 | 0 | 2 | 0 |

Planner: 9 examples.

| | untuned | tuned (step 200) | perfect answers |
|---|---|---|---|
| Valid JSON | 5 of 9 | 9 of 9 | 9 of 9 |
| Exactly the rule planner's pick | 0 of 9 | 9 of 9 | 9 of 9 |
| Outside the schema (target 0) | 9 | 0 | 0 |
| After the guard | fell back to the rule planner 9 times | acted on 9 | acted on 9 |

How to read it:

- Tuning taught the shape of the answer. The untuned model wrote 73 answers outside the allowed
  shapes and refused no refusal question itself. The tuned model wrote none outside and refused
  all 48.
- The tuned model refuses too much. It refused 16 of the 30 answerable questions itself, 9 of
  them where the keyword matcher has an answer. That is why it loses to the matcher.
- The guard dropped 6 of its 13 right answers: 4 because the guard's own checks refuse the
  question whatever the model says, and 2 because the matcher gave another answer and the matcher
  wins. In those 2 the matcher was wrong.
- Where the matcher is silent (14 answerable questions), the tuned model's answer was right on 7,
  and 4 of those pass the guard. The matcher gets none of them. That is the only place the model
  adds something.
- Counted after the fact, and not a planned metric: the tuned model first, then the matcher when
  the model's answer is refused, gives 18 right, 2 wrong and 10 refused on the 30 answerable
  questions, and acts on 2 refusal questions. The matcher alone gives 14 right, 2 wrong and 14
  refused, and acts on the same 2. Both wrong answers and both refusal questions acted on are the
  matcher's. The matcher row in the table is 13, not 14, because the tuning guard refuses one
  question that the matcher answers right.
- The planner result is weak evidence: the task is close to copying (see Limits), and 9 examples
  is very few.

Do not read the training loss above as a result: it shows the adapter learned the training
examples, not that it answers new questions.

Fixed before any model was run, with no model involved (`python -m research.agent_tuning.score --baseline ...`):

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

- The evaluation is small: 78 questions and 9 planner examples, one run, greedy decoding, no error
  bars. Read the counts as a first look, not a measurement.
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
