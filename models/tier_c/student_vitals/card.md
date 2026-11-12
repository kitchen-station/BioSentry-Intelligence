# Distilled vitals student (Tier C, research only)

| | |
|---|---|
| id | `tier_c/student_vitals` |
| files | `model.onnx` (19 features), `model_no_temp.onnx` (15 features), `metadata.json` |
| code | `research/student.py`, `research/train_student.py`, `research/evaluate_student.py` |
| status | trained, synthetic, not validated. Not wired to the API, the rank or the arbiter |
| intended use | research. Tier A (NEWS2) is never changed or learned |

## What it is

A tiny neural network (one hidden layer of 16 ReLU units, then a sigmoid) that gives, for each
15-minute bin, a probability that the patient is in the onset of a deterioration episode. It is
a small network fitted to the log-odds of a weak, overfitted teacher (gradient boosting). Call it
distilled only with that caveat: the teacher is worse than a constant guess on validation, and
the student beats it. Only the student is exported.

## Data

Synthetic only: ward-v1 simulator trials (sim commit 3a15e01), 30 simulated days each. Seeds 1
to 7 train the teacher and the students. Seeds 8 to 10 pick the early stop, the student and the
threshold. Seeds 11 to 30 were scored once, after everything was fixed. The training loader
opens only `manifest.json` of a held-out folder and skips it; a test proves it.

## Features (per bin, causal)

Per device vital (heart rate, respiratory rate, SpO2, temperature): the value, a missing flag,
z from the patient's own baseline (the backend's `baseline_finding`, on bins up to this one),
and a z-missing flag (baseline still forming, or value missing). Then the NEWS2 total, on
oxygen, and consciousness not A. A missing input is 0 with its flag set. Nothing is filled in.
On these trials a scored bin always has all four device vitals, so the value-missing flags are
always 0. NOT RANKED bins are not scored: no probability, not alerting (abstention is an output).

The no-temperature model drops the four temperature features and uses the NEWS2 total minus its
temperature points. It has no temperature input at all (tested).

The four `*_z_missing` flags are set for the first 6 h of a stay and carry the largest
first-layer weights. Simulator episodes rarely start in the first 6 h (on seeds 8 to 10, 0% of
positive bins and 5.8% of negative bins have a flag set), so the model partly learns "not in the
first 6 hours". That is a simulator artefact.

## Label

Positive from the generator's true `episode_start` to fully in (start + ramp). Bins after fully
in are left out of training. All other bins are negative. `outcome`/higher care is never a
label. Starting patients (admit minute 0) are left out. Training: 211,333 bins, 597 positive
(0.28%). Validation: 86,819 bins, 131 positive.

## Recipe

- Teacher: `HistGradientBoostingClassifier`, `random_state=0`, early stopping on the loss of
  seeds 8 to 10. It stopped after 20 iterations: its validation loss never improved after the
  first. scikit-learn keeps all 20, so the teacher is overfitted. On seeds 8 to 10 it is worse
  than a constant guess: log loss 0.035 (0.039 unclipped) against 0.012 for the training
  prevalence, average precision 0.005. The student scores 0.036. A better-regularised teacher
  was not tried.
- Students, fitted to the teacher's log-odds on seeds 1 to 7 (soft labels): logistic (ridge) and
  MLP (16 units). Picked by average precision on seeds 8 to 10. Standardisation constants are in
  the graph.
- Validation average precision (seeds 8 to 10, prevalence 0.0015):

| | teacher | logistic | MLP (kept) |
|---|---|---|---|
| all features | 0.005 | 0.002 | 0.036 |
| no temperature | 0.029 | 0.002 | 0.013 |

- Threshold: the smallest of 0.001 to 0.99 whose alerts on seeds 8 to 10 do not exceed the NEWS2
  chart trigger's (805). Student 0.002 (416 alerts), teacher 0.003 (761). No temperature: both
  0.001 (725 and 777), the bottom of the grid. There is no usable threshold for them: at 0.001
  the no-temperature student is in alert 52.7% of the time on seeds 8 to 10 (NEWS2: 12.4%). One
  alert stays open, because the alert rule re-arms only after 4 h not alerting, so the alert
  count looks low while the patient is alerting half the time.

## Held-out numbers (seeds 11 to 30)

91 episodes, 79 not cut short, 4,678 chance windows, 1,873 patients. Any-bin = alerting in any
bin of the window, episodes not cut short. Excess = any-bin minus chance. Lead time = median
minutes before fully in, episodes with a new alert.

| Policy | Alerts / patient / 12 h | Time in alert | Any-bin | Chance | Excess | New alert | Lead time |
|---|---|---|---|---|---|---|---|
| NEWS2 chart trigger | 0.413 | 0.159 | 0.709 | 0.472 | +0.237 | 0.418 | 45.9 |
| Baseline alone (B4) | 0.189 | 0.047 | 0.848 | 0.178 | +0.670 | 0.785 | -18.8 |
| NEWS2 OR baseline (B4) | 0.473 | 0.189 | 0.924 | 0.550 | +0.374 | 0.570 | 20.5 |
| Teacher | 0.381 | 0.029 | 0.696 | 0.240 | +0.456 | 0.620 | 11.0 |
| Student | 0.226 | 0.036 | 0.696 | 0.157 | +0.539 | 0.633 | 20.5 |
| NEWS2 OR student | 0.457 | 0.173 | 0.810 | 0.509 | +0.301 | 0.494 | 44.6 |
| Teacher, no temperature | no usable threshold: alert stuck open (about 50% of time on seeds 8-10); not a working rule | | | | | | |
| Student, no temperature | no usable threshold: alert stuck open (about 53% of time on seeds 8-10); not a working rule | | | | | | |
| NEWS2 OR student, no temperature | no usable threshold: alert stuck open (about 53% of time on seeds 8-10); not a working rule | | | | | | |

The no-temperature rows have no new-alert excess on held-out (-0.009 to +0.004).

The untrained baseline rule from B4 beats the student on held-out. It has more excess (+0.670
against +0.539; without temperature +0.420 against +0.116) and fewer alerts (0.189 against
0.226 per patient per 12 h). The student is better only on lead time (20.5 against -18.8 min).
On this simulator the learned model is not better than the simple rule.

95% intervals and per-kind rows are in the `research.evaluate_student` output.

## What depends on temperature

Every useful number depends on the simulator's uncited temperature rise. The simulator adds a fixed,
uncited temperature rise to every episode (see the baseline card). With temperature, the student
beats NEWS2 alone on excess (+0.539 against +0.237) at about half the alerts. Without
temperature, the student is in alert 54% of the time on the held-out seeds (53% on seeds 8 to 10), so chance is 0.834 and the excess is
+0.116, below NEWS2. Its alert count looks low only because one long alert stays open. On this
simulator, the student adds nothing measurable over NEWS2 once temperature is removed.

The model is a fragile combination. Shuffling any one of the four vitals on seeds 8 to 10
collapses its average precision from 0.036 to about 0.003 (0.0015 to 0.0040).

## Limits

- Circular: trained and judged on the same simulator. An upper bound, not a performance claim.
- Synthetic, not validated. No claim about real patients.
- Excess is capped at 1 minus chance, so compare it only between rows with similar chance.
- Alerts per 12 h ignores how long an alert stays open. Read it with time in alert.
- 597 positive training bins. Small differences between rows are a few episodes.
- The no-temperature models have no usable threshold (alert stuck open, see above).
- "Scored once" is by procedure; the code does not stop a second run.
- Not wired to the API, the rank or the arbiter.

## Huawei fit

ONNX opset 13, IR version 7, float32, static input [1, n], output [1, 1]. Ops: Sub, Div, Gemm,
Relu, Sigmoid. Built with `onnx.helper`; `onnx.checker` passes; onnxruntime matches the numpy
forward pass within 1e-5 on 1,000 training rows (`metadata.json`). Converting to MindSpore Lite
(`converter_lite --fmk=ONNX`) is a Linux deployment step. It has not been done here.

| File | Bytes | sha256 |
|---|---|---|
| `model.onnx` | 1,806 | `e7ec927c77c625465fcdc28d66736024e0a417ab88cd66f0c3e5c41fca85613d` |
| `model_no_temp.onnx` | 1,518 | `a2ae213977e13d8eb864fe685bceeed83e8cdb89637f823a4a783fb92d153546` |

## Reproduce

Inside `biosentry-research-venv` (see `research/README.md`, ML gate):

    python -m research.train_student /data/trials/ward-v1/seed-*
    python -m research.evaluate_student /data/trials/ward-v1/seed-*

Two full training runs wrote the same sha256s. About 2.5 minutes per training run.

## Packages (exact pins, `research/uv.lock`)

Direct: numpy 2.4.6 (BSD-3-Clause and others), scikit-learn 1.9.1 (BSD-3-Clause), onnx 1.23.2
(Apache-2.0), onnxruntime 1.31.1 (MIT). The backend's own pins, needed to import its NEWS2 and
run the tests: pydantic 2.14.1 (MIT), pytest 9.1.1 (MIT). Pulled in: scipy 1.17.1 (BSD), joblib
1.6.0 (BSD-3-Clause), threadpoolctl 3.8.0 (BSD-3-Clause), protobuf 7.36.2 (BSD-3-Clause),
flatbuffers 25.12.19 (Apache-2.0), ml-dtypes 0.6.0 (Apache-2.0), narwhals 2.28.0 (MIT),
cloudpickle 3.1.2 (BSD-3-Clause), packaging 26.3 (Apache-2.0 or BSD-2-Clause), typing-extensions
4.16.0 (PSF-2.0), pydantic-core 2.50.1, annotated-types 0.8.0, typing-inspection 0.4.5,
iniconfig 2.3.1, pluggy 1.7.0 (all MIT), Pygments 2.21.0 (BSD-2-Clause).
Vulnerability check (pip-audit on the locked requirements, 2026-11-12): "No known
vulnerabilities found".
