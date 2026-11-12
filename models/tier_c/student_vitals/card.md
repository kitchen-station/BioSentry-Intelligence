# Distilled vitals student (Tier C, research only)

| | |
|---|---|
| id | `tier_c/student_vitals` |
| files | `model.onnx` (19 features), `model_no_temp.onnx` (15 features), `metadata.json` |
| code | `research/student.py`, `research/train_student.py`, `research/evaluate_student.py` |
| status | trained, synthetic, not validated. Not wired to the API, the rank or the arbiter |
| intended use | research. Tier A (NEWS2) is never changed or learned |

## What it is

A small model that gives, for each 15-minute bin, a probability that the patient is in the onset
of a deterioration episode. On ward-v2 the shipped `model.onnx` is a logistic student (no hidden
layer), fitted to the log-odds of an overfitted gradient-boosting teacher. The teacher has higher
validation average precision (0.008 against 0.003); the student has lower log loss. Only the
student is exported. (On ward-v1 it was an MLP with one hidden layer of 16 ReLU units.)

## Result on ward-v2 (temperature rise removed, noise 0.1 C)

The committed files are now trained on ward-v2: ward-v1 made again by the fixed simulator (sim
commit f7647c5, after PR #25 removed `INJECT_TEMPERATURE` and raised temperature noise from
0.02 to 0.1 C). Same seeds, scenario, days and command; data card `models/data/ward-v2/card.md`. Same recipe, split, threshold rule and
harness. Nothing was tuned again.

- Validation average precision (seeds 8 to 10, prevalence 0.0015): teacher 0.008, logistic
  0.0033, MLP 0.0030. So the logistic student is kept this time, by a margin that is noise. No
  temperature: teacher 0.006, logistic 0.008, MLP 0.013 (MLP kept). The MLP fit stops at
  scikit-learn's 100 iterations with a convergence warning (the recipe's setting, not changed).
- Thresholds: student 0.002 (158 alerts on seeds 8 to 10 against NEWS2's 806). The teacher at
  0.001 is in alert 43% of the time on seeds 8 to 10 and both no-temperature models 81%: no
  usable threshold, not working rules.
- Held-out seeds 11 to 30, scored once (91 episodes, 79 not cut short, 4,678 chance windows):

| Policy | Alerts / patient / 12 h (95% CI) | Time in alert | Any-bin | Any-bin, all (95% CI) | Chance | Excess | New-alert excess | Lead |
|---|---|---|---|---|---|---|---|---|
| NEWS2 chart trigger | 0.414 (0.395-0.434) | 0.158 | 0.684 | 0.626 (0.522-0.727) | 0.473 | +0.210 | +0.179 | 47.2 |
| Baseline alone (B4, k 4.0, p 8) | 0.042 (0.035-0.049) | 0.012 | 0.278 | 0.264 (0.174-0.360) | 0.032 | +0.246 | +0.253 | -82.5 |
| NEWS2 OR baseline (B4) | 0.426 (0.407-0.444) | 0.165 | 0.722 | 0.659 (0.558-0.758) | 0.486 | +0.235 | +0.210 | 39.6 |
| Student (logistic) | 0.088 (0.077-0.099) | 0.019 | 0.367 | 0.352 (0.250-0.452) | 0.058 | +0.309 | +0.312 | 5.2 |
| NEWS2 OR student | 0.429 (0.410-0.448) | 0.165 | 0.709 | 0.648 (0.545-0.745) | 0.486 | +0.223 | +0.200 | 47.2 |
| Teacher; all no-temperature rows | no usable threshold: alert stuck open (43% to 81% of time on seeds 8-10); not a working rule | | | | | | | |

What this shows:
- Added to NEWS2, the student gives nothing measurable: 56 of 79 against NEWS2's 54, with
  overlapping intervals.
- Alone, it catches about half as many episodes as NEWS2 (any-bin over all episodes 0.352 against
  0.626, intervals do not overlap) at about a fifth of the alerts. Its excess (+0.309) is above
  NEWS2's (+0.210), but excess is capped at 1 minus chance (0.942 against 0.527), so the two are
  not comparable.
- On ward-v1 the student alone had any-bin 0.637 (0.527-0.731) over all episodes and excess
  +0.539. The gains seen on ward-v1 went away on ward-v2. Two things changed (the temperature rise was removed and temperature noise went from 0.02 to 0.1 C), and this run cannot say which one caused it.

What it cannot show: anything about patients, or whether a better teacher would help (the teacher
is still worse than a constant guess on validation, log loss 0.021 against 0.011).

## Data

Synthetic only: ward-v2 simulator trials (sim commit f7647c5), 30 simulated days each. The
sections below, from "Features" to "What depends on temperature", describe the earlier ward-v1 run
(sim commit 3a15e01, with the temperature rise) and are kept for comparison. Seeds 1
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

## Held-out numbers on ward-v1 (seeds 11 to 30; kept for comparison)

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

On ward-v1 the B4 rule had fewer alerts than the student (0.189 against 0.226); their excess cannot be compared because their chance rates differ.

95% intervals and per-kind rows are in the `research.evaluate_student` output.

## What depends on temperature (ward-v1)

On ward-v1, every useful number depended on the simulator's uncited temperature rise. The simulator then added a fixed,
uncited temperature rise to every episode (see the baseline card). With temperature, the student
had a higher excess than NEWS2 (+0.539 against +0.237), but at a much lower chance rate, so this does not rank them. Without
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
Relu, Sigmoid (the ward-v2 `model.onnx` is the logistic student, so it has no Relu). Built with `onnx.helper`; `onnx.checker` passes; onnxruntime matches the numpy
forward pass within 1e-5 on 1,000 training rows (`metadata.json`). Converting to MindSpore Lite
(`converter_lite --fmk=ONNX`) is a Linux deployment step. It has not been done here.

| File | Bytes | sha256 |
|---|---|---|
| `model.onnx` (ward-v2) | 470 | `41deaa6c07c366aee5fe5bac47c6edc8282eb5bf1fe1dbfbc89e8af887dd1ecd` |
| `model_no_temp.onnx` (ward-v2) | 1,518 | `af7453e1412b0f6bcb4cd22ef7b6246738929c255b77db61e9565ee1fdffcef5` |

The ward-v1 files (`model.onnx` 1,806 bytes, sha256 `e7ec927c...`; `model_no_temp.onnx` `a2ae2139...`) are in git history before this change.

## Reproduce

Inside `biosentry-research-venv` (see `research/README.md`, ML gate):

    python -m research.train_student /data/trials/ward-v2/seed-*
    python -m research.evaluate_student /data/trials/ward-v2/seed-*

`evaluate_student` retrains and refuses unless the bytes match the committed sha256s; on ward-v2
it ran and matched, so a second training gave the same bytes. About 2.5 minutes per training run.

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
