# Distilled vitals student (Tier C, research only)

| | |
|---|---|
| id | `tier_c/student_vitals` (variants `all` and `no_temp`) |
| files | `model.onnx` (5,334 bytes), `model_no_temp.onnx` (2,382), `model.mindir` (11,539), `model_no_temp.mindir` (8,706), `model.ms` (6,688), `model_no_temp.ms` (3,744), `model.json` (34,949), `model_no_temp.json` (14,903), `metadata.json`, `exports.json`, `timing.json`, `lite.txt`, `convert_lite.sh`, `held_out.txt`, `held_out.json` |
| code | `research/student.py`, `research/train_student.py`, `research/evaluate_student.py`, `research/export_plain.py`, `research/plain_student.py` |
| status | trained on synthetic trials; held-out ward-v2 seeds 11-30 scored once on 2026-11-13; not validated, not wired to the API, the rank or the arbiter |
| framework | MindSpore 2.11.0 (CPU, GRAPH_MODE, one thread) |
| intended use | research. Tier A (NEWS2) is never changed or learned |

Held-out ward-v2 seeds 11-30 were scored once, on 2026-11-13. The outputs are `held_out.txt` (the printed table) and
`held_out.json` (format `biosentry-held-out/1`, full precision). At NEWS2's alert count the student alerts in about as
many episode windows as NEWS2, with less time in alert and a lower chance rate. Added to NEWS2 it alerts in 11 more of
91 episode windows at 1.27 times NEWS2's alerts; no gain beyond chance is shown. The old scikit-learn student (B5)
and its numbers are in git history.

## What the pitch line says and what is true now

- "A small neural network": true. One hidden ReLU layer. `all`: 35 inputs, width 32, 1,185 parameters (plus 70
  standardisation constants), `model.onnx` 5,334 bytes. `no_temp`: 27 inputs, width 16, 465 parameters (plus 54),
  2,382 bytes.
- "Trained inside the simulation": true. Simulator trials only: ward-v2-ext seeds 61-160 (100 trials) train, seeds
  261-300 (40 trials) validate. No real patient data. The held-out seeds are simulator trials too.
- "Distilled from a much bigger model": true as a procedure, not as a gain. The student is fitted to the teacher's
  logits and never sees a label. The teacher is a 256x256 MindSpore network with 79,873 parameters, 67 times the
  student's (76,801 and 165 times for `no_temp`). It passed its pre-registered gate. BUT the distilled student's
  validation log loss (0.018969) is not better than the same small network fitted on the true labels (0.018925). The
  recorded difference, student minus best hard-label network, is +4.4e-05 (95% bootstrap -9.3e-05 to 0.000193);
  `no_temp` +4.6e-05 (-7.2e-05 to 0.000169). So distillation bought nothing measurable here. The rules ship the
  distilled student because the teacher gate passed, not because it won. On held-out the student and the teacher
  alert in about as many episode windows (0.670 and 0.648 over all episodes, intervals overlap).
- "Every fifteen minutes it gives the probability that a patient is starting to deteriorate": true, with a definition.
  One probability per scored 15-minute bin. The label is positive from the true episode start to fully in. NOT RANKED
  bins get no probability. On held-out, as an alert rule at t 0.006, it is not shown to add to NEWS2 (below).
- "Small enough to run on the ward hub, so it works without internet": partly shown. The files are small (above). One
  ONNX call with one row takes a median of 8.58 us (p99 26.63 us); `no_temp` 8.37 us (p99 29.75 us), onnxruntime
  1.31.1, one thread, on this Apple Silicon Docker host (aarch64). The MindSpore Lite `.ms` conversion was done and
  checked (below). A ward needs one call per patient per 15 minutes: about 13 calls (the validation trials average
  about 12.7 patients at a time). It was NOT run on a small ARM board or a ward hub. The per-patient feature
  computation is separate and was not timed. Held-out says nothing about this.

## Held-out numbers (ward-v2 seeds 11 to 30, scored once on 2026-11-13)

91 episodes, 79 not cut short, 4,678 control windows, 1,873 patients (20 trials, 7,565.8 patient-days). Before it
scored, the run refitted the chosen teacher and student and matched their weight and ONNX sha256 to `metadata.json`.
Thresholds (from validation, unchanged): student 0.006 for both variants, teacher 0.006 (`all`) and 0.005 (`no_temp`).
Their time in alert on validation is in the threshold table below (student 6.6% and 6.7%).

- Any-bin: alerting in any bin of the window, episodes not cut short. Any-bin, all: over all 91 evaluated episodes,
  with its 95% interval (patient-level bootstrap, 1,000 resamples).
- Chance and excess: for any-bin, episodes not cut short. Excess = any-bin minus chance.
- New-alert excess: the new-alert rate over episodes not cut short minus `chance_new` (the share of control windows
  with a new alert), from the JSON fields `new_alert_not_cut_short.rate` and `chance_new.rate`. It equals the JSON
  field `excess_new_not_cut_short`.
- Lead: median minutes before fully in, episodes with a new alert.

| Policy | Alerts/pt/12 h (95% CI) | Time in alert | Any-bin | Any-bin, all (95% CI) | Chance | Excess | New-alert excess | Lead |
|---|---|---|---|---|---|---|---|---|
| NEWS2 chart trigger | 0.414 (0.395-0.434) | 0.158 | 0.684 | 0.626 (0.522-0.727) | 0.473 | +0.210 | +0.179 | 47.2 |
| B4 baseline alone (k 4.0, p 8) | 0.042 (0.035-0.049) | 0.012 | 0.278 | 0.264 (0.174-0.360) | 0.032 | +0.246 | +0.253 | -82.5 |
| NEWS2 OR B4 | 0.426 (0.407-0.444) | 0.165 | 0.722 | 0.659 (0.558-0.758) | 0.486 | +0.235 | +0.210 | 39.6 |
| Teacher, t 0.006 | 0.391 (0.374-0.408) | 0.069 | 0.709 | 0.648 (0.544-0.742) | 0.307 | +0.401 | +0.399 | 49.6 |
| Student, t 0.006 | 0.413 (0.394-0.430) | 0.064 | 0.734 | 0.670 (0.570-0.762) | 0.319 | +0.415 | +0.389 | 59.5 |
| NEWS2 OR student | 0.525 (0.507-0.543) | 0.185 | 0.823 | 0.747 (0.655-0.831) | 0.580 | +0.243 | +0.209 | 66.0 |
| B4 alone, without temperature | 0.032 (0.026-0.038) | 0.011 | 0.266 | 0.253 (0.165-0.343) | 0.025 | +0.241 | +0.248 | -85.3 |
| NEWS2 OR B4, without temperature | 0.425 (0.405-0.442) | 0.164 | 0.709 | 0.648 (0.550-0.745) | 0.484 | +0.225 | +0.199 | 44.6 |
| Teacher `no_temp`, t 0.005 | 0.423 (0.406-0.441) | 0.084 | 0.759 | 0.703 (0.602-0.795) | 0.357 | +0.402 | +0.351 | 28.4 |
| Student `no_temp`, t 0.006 | 0.409 (0.391-0.427) | 0.065 | 0.747 | 0.681 (0.576-0.773) | 0.329 | +0.418 | +0.391 | 48.8 |
| NEWS2 OR student `no_temp` | 0.512 (0.494-0.530) | 0.183 | 0.823 | 0.747 (0.651-0.835) | 0.577 | +0.246 | +0.214 | 66.0 |

Chance rates and excess have no interval. The full output, with the NEWS2 total >= 5 row, new-alert rates and
per-kind rows, is `held_out.txt`.

What this shows:
- The student's threshold was matched on validation to NEWS2's alert count. On held-out it raises about the same
  number: 0.413 (0.394-0.430) alerts per patient per 12 h against NEWS2's 0.414 (0.395-0.434). At that count it is
  alerting in about as many episode windows: 0.670 (0.570-0.762) over all episodes against NEWS2's 0.626
  (0.522-0.727). The intervals overlap. Its chance rate is lower (0.319 against 0.473) and its time in alert is lower
  (0.064 against 0.158). Chance rates have no interval.
- Its excess is higher, +0.415 against +0.210. But excess is capped at 1 minus chance: 0.681 for the student, 0.527
  for NEWS2. As a share of its cap, the student's excess is 0.61 and NEWS2's is 0.40. Because the caps differ, the
  two rules cannot be ranked by excess alone.
- Added to NEWS2, the student alerts in 11 more of 91 episode windows (68 against 57) at 1.27 times NEWS2's alerts
  (0.525 against 0.414 per patient per 12 h). NEWS2 OR X can only add windows to NEWS2. Most of that is what the
  extra alerts give by chance: chance rises from 0.473 to 0.580, and the excess over chance is +0.243 against +0.210
  for NEWS2 alone. Excess has no interval. No gain beyond chance is shown. (As rates over all episodes: 0.747
  (0.655-0.831) against 0.626 (0.522-0.727); separate intervals do not test this paired gain.)
- Lead time: the student's median is 59.5 minutes before fully in (interquartile range 6.2 to 191.5, 51 episodes with
  a new alert), NEWS2's 47.2 (-6.8 to 458.5, 33 episodes). NEWS2 OR student is 66.0. Medians have no interval.
- New alerts over all episodes: student 0.560 (0.460-0.663), NEWS2 0.363 (0.260-0.459). These intervals miss each
  other by less than 0.001. NEWS2 had an alert already open in 24 episodes, so it raises fewer new alerts inside
  the window by construction. Treat this as weak.
- The teacher and the student are within noise of each other: any-bin over all episodes 0.648 (0.544-0.742) and
  0.670 (0.570-0.762), at 0.391 and 0.413 alerts. The teacher's better validation loss does not show here.
- Without temperature the rows tell the same story: student `no_temp` 0.681 (0.576-0.773) at 0.409 alerts. Added to
  NEWS2 it alerts in 11 more of 91 windows (68 against 57) at 1.24 times NEWS2's alerts; chance 0.577 against 0.473,
  excess +0.246 against +0.210. No gain beyond chance is shown.
- Hypotensive episodes (27; 17 with no cuff reading inside the window): any-bin NEWS2 9, student 7, NEWS2 OR student
  13; new alert NEWS2 2, student 5, NEWS2 OR student 4. Few events; no claim. With the confidence check the student
  alerts in 0 of 27 hypotensive episodes (both variants; see `tier_c/confidence_check/card.md`).

What it cannot show:
- Anything about patients. These are simulated wards.
- Whether the lower chance rate at an equal alert count would hold on another simulator setting or on real data.
- The same held-out seeds were used earlier for earlier models of this project (B5 and others). By procedure this
  model was read on them once. `evaluate_student` writes to standard output and has no run-once guard in code.
- Held-out was not used to choose anything: the model, the variant and the threshold were fixed before the run.

## Validation results (seeds 261-300)

Validation log loss (lower is better) and average precision (AP, recorded where the run measured it; never used to
choose). Validation prevalence 0.0031 (3,795 positive of 1,207,464 rows).

| Model | `all` log loss | `all` AP | `all` parameters | `no_temp` log loss | `no_temp` AP | `no_temp` parameters |
|---|---|---|---|---|---|---|
| Teacher (256x256, decay 1e-4) | 0.018699 | 0.118078 | 79,873 | 0.018889 | 0.107075 | 76,801 |
| Distilled student (shipped) | 0.018969 | 0.096846 | 1,185 | 0.019154 | 0.087059 | 465 |
| Best hard-label student (trend, width 8) | 0.018925 | 0.09227 | 297 | 0.019108 | 0.086833 | 233 |
| Constant (training prevalence) | 0.021279 | not recorded | - | 0.021279 | not recorded | - |
| Old gradient-boosting recipe (b) | 0.021839 | not recorded | - | 0.021944 | not recorded | - |
| Regularised gradient boosting (d), recorded only | 0.018931 | not recorded | - | 0.019104 | not recorded | - |

Teacher gate (teacher minus reference, patient-level bootstrap, 1,000 resamples, seed 20261112; "below" means the
upper bound is under 0). Passed for both variants.

| Reference | `all` difference (95%) | `no_temp` difference (95%) |
|---|---|---|
| (a) constant | -0.00258 (-0.003547 to -0.001749) | -0.002389 (-0.0034 to -0.001527) |
| (b) old recipe | -0.003141 (-0.003874 to -0.002459) | -0.003054 (-0.00393 to -0.002279) |
| (c) best hard-label student | -0.000227 (-0.000451 to -3.4e-05) | -0.000219 (-0.000471 to -7e-06) |

The student also beats the constant: -0.002309 (-0.003131 to -0.001633); `no_temp` -0.002125 (-0.002907 to -0.001405).
The teacher is better than (d). The student is not: (d) is 0.018931 against the student's 0.018969.

Checked by a second agent, before the held-out reads: it refitted the teacher, the best hard-label student and the old
gradient-boosting recipe with its own loss code and its own patient-level bootstrap. The refitted weights' sha256
equalled `metadata.json`, and every loss and every gate bound above matched. It also found:
- Student minus hard-label student: +0.000044 (-0.000093 to +0.000193) for `all`, +0.000046 (-0.000072 to +0.000169)
  for `no_temp`. Indistinguishable.
- Student minus teacher: +0.000271 (+0.000120 to +0.000470) for `all`, +0.000264 (+0.000092 to +0.000478) for
  `no_temp`. The student is worse than its teacher on validation. `metadata.json` has the point value only
  (`log_loss_minus_teacher`), not the interval.
- Reference (b), the old recipe, stops at 20 iterations and is worse than the constant. Passing it adds no evidence
  beyond the constant.
- The `no_temp` margin against the hard-label student is thin: its upper bound is -0.000007.
- The gate is scored on the same seeds that chose the models. That is optimistic, as pre-registered.

The size of the gain, in plain words: the teacher's log loss is about 12% below the constant's, the student's about
11%. The student's AP is about 0.097 against a prevalence of 0.003. That is a real but weak signal: most bins above
any useful threshold are still negative.

## Data and split

- ward-v2-ext simulator trials (data card `models/data/ward-v2-ext/card.md`), 30 days each, scenario sha256
  `95052d02...98a8`. Synthetic only.
- Seeds 61-160 train the teacher, the students and the standardisation constants: 3,044,556 rows, 8,327 positive
  (0.2735%). Seeds 261-300 validate (early stopping, every model choice, the threshold): 1,207,464 rows, 3,795
  positive (0.3143%). So about 0.3% of scored bins are positive.
- Seeds 301-340 (the confidence check) and ward-v2 folders (seeds 1-60) are not read in training: the loader opens
  only their `manifest.json`. Held out: ward-v2 seeds 11-30, through `evaluate_student`, scored once on 2026-11-13.

## Features, label

- Causal: bin t reads only bins up to t of the same stay. Never an input: time since admission, patient id, seed,
  anything from `episodes.csv`, `outcome`. Missing is the number 0 and a set flag; nothing is imputed.
- Base (19; 15 without temperature): per vital (heart rate, respiratory rate, SpO2, temperature) the value, a missing
  flag, z from the patient's own baseline, a z-missing flag; then the NEWS2 total, on oxygen, consciousness not A.
  `no_temp` drops the four temperature inputs and uses the NEWS2 total minus its temperature points.
- "Consciousness not A" and the value-missing flags were constant 0 in the training trials. So the student
  ignores them in practice: it cannot react to a change in consciousness or to a missing value.
- Trend (16 more; 12 without temperature): per vital the change over 1 h and over 4 h, each with a missing flag. Both
  shipped students use base plus trend: 35 inputs (`all`), 27 (`no_temp`).
- Teacher (53; 41): base and trend, then per vital the 4-hour mean and slope with flags, and the change of the NEWS2
  total over 1 h with a flag.
- Label: positive from `episode_start` to fully in (start plus ramp). Bins after fully in are left out. Starting
  patients (admit minute 0) are left out. `outcome` is never a label.

## Recipe (pre-registered in `research/README.md`, slice M1)

- Teacher: n-256-256-1 ReLU MLP, BCE on the true labels, Adam 1e-3, batch 1024, at most 15 epochs, patience 3 on
  validation log loss. Decay 1e-4 was chosen (best epoch 9 of 12 for `all`, 8 of 11 for `no_temp`).
- Distilled students: one hidden layer, width {8, 16, 32} x inputs {base, trend}, MSE on the teacher's logits clipped
  to +-9.2102, Adam 1e-3, decay 1e-5, batch 4096, at most 8 epochs, patience 2. Hard-label students (reference c): the
  same 6 networks with BCE on the true labels.
- Selection: validation log loss against the true labels. Values within 1e-6 tie; then fewer parameters, then base
  over trend, then the larger decay.
- Threshold: the smallest t in the grid (0.001 to 0.009, then 0.01 to 0.99) whose alerts AND time in alert on seeds
  261-300 are both at most NEWS2's. Chosen: student 0.006 (both variants); teacher 0.006 (`all`) and 0.005
  (`no_temp`).

| Rule (seeds 261-300, 1,458,487 cohort bins) | Alerts | Alerting bins | Time in alert |
|---|---|---|---|
| NEWS2 chart trigger | 12,852 | 249,871 | 17.1% |
| Student `all`, t 0.006 | 12,633 | 96,951 | 6.6% |
| Student `no_temp`, t 0.006 | 12,565 | 97,863 | 6.7% |
| Teacher `all`, t 0.006 | 11,667 | 106,842 | 7.3% |
| Teacher `no_temp`, t 0.005 | 12,851 | 129,697 | 8.9% |

The full grid of 14 fits (validation log loss, AP, parameters, losses by epoch) is in `metadata.json`
(`variants.<variant>.grid`). Per-epoch losses and wall times: `training/runs/student_vitals/m1-20261113-3d634f3.json`.

## Exports, and which tool wrote which file

- `*.mindir`: MindSpore 2.11.0 `mindspore.export` from the trained network.
- `*.onnx`: `onnx.helper` from the same MindSpore-trained weights. Opset 13, IR 7, ops Sub, Div, Gemm, Relu, Gemm,
  Sigmoid, input `x` [1, 35] or [1, 27], output `p` [1, 1]. MindSpore's own ONNX export gave opset 11 / IR 4 in a
  probe, and the project fixes opset 13 / IR 7.
- `*.ms`: MindSpore Lite 2.11.0 `converter_lite` from the `.mindir`, by hand with `convert_lite.sh` in the project's
  Docker image (linux aarch64), with the official linux-aarch64 package (tarball sha256
  `886186849c3291933b7265ebaa0d51df2bb175e7cbb32b488795ea19a96b2d6c`, measured on the downloaded file, not compared
  with a published checksum). Research Python starts no external program, so this is a separate script. Raw output:
  `lite.txt`.
- `*.json`: `python -m research.export_plain` from the ONNX (format `biosentry-plain-mlp-1`).

| File | Bytes | sha256 |
|---|---|---|
| `model.onnx` | 5,334 | `d1ae4e881732922024b08c6cf9c37adbd8b01c48bc8390ee22b28ab802ddfdbc` |
| `model_no_temp.onnx` | 2,382 | `e6b6ae44c11aa38fc38a961eebaf3e3bd51b1b5812b9e200d31d8c04101869ee` |
| `model.mindir` | 11,539 | `cbc9c82ae041e5bc01adf7781d81c79339bf26820c646748cfa6ab795ca309b1` |
| `model_no_temp.mindir` | 8,706 | `6ac94a6b5bee43689187d739b171f91858f96d10e4b11b652ac9c3812e3faa6d` |
| `model.ms` | 6,688 | `4ca0d0deb3a20835dacd1bc50c3981bae65111051ecc8817265d9e9f4fc06509` |
| `model_no_temp.ms` | 3,744 | `fb023119cb8038dfdd174df10d3097e26413a0711b6f76af6ee15f975e445e5a` |
| `model.json` | 34,949 | `d05d75ee0ad8da0db138a74f3a9796c98de6b8af5dfea513789d5521f1768af6` |
| `model_no_temp.json` | 14,903 | `d041b6584edc7d67c54dcdb03e81f0c1a58f51282a3cf02fbc3e869ff53cb2fc` |

Parity (largest absolute p difference, tolerance 1e-5):
- Training run, 1,000 fixed training rows: MindSpore against ONNX 9.4e-08, MindSpore against MindIR 1.5e-08, MindIR
  against ONNX 9.4e-08; `no_temp` 9e-08, 4.5e-08, 9e-08.
- Checked by a second agent, 1,000 real validation rows: MindIR loaded back against ONNX 2.4e-07 (`all`) and 1.2e-07
  (`no_temp`); plain JSON forward against ONNX 9.2e-08 and 9.7e-08.
- Lite: both conversions `CONVERT RESULT SUCCESS:0`; 20 of 20 rows within 1e-5 of MindSpore for both. Lite benchmark
  average run time 0.001 ms and 0.002 ms (one thread, this Apple Silicon Docker host).

Checked by a second agent from the committed ONNX files only, with its own code: the log losses, the constant, the row
and positive counts, AP 0.0968 and 0.0871, mean p 0.00256 against prevalence 0.00314 (`all`), the threshold (at 0.005:
14,682 alerts, above NEWS2), parameter counts and ratios (67.4, 165.2), opset, IR, ops, shapes, and the standardisation
constants (largest mean difference from the training-row moments 1.4e-06). Its refit of the teacher gate is in the
validation section above.

## Reproducibility

- Promised (on linux aarch64, one thread): two full runs give identical weights, ONNX and `metadata.json` bytes.
  MindIR bytes are not promised (a timestamp inside).
- Shown: run m1-20261113-3d634f3 took 85 minutes (5,090 s; projected 10,354 s, under the 10,800 s budget, so no cut).
  A second full run on the same machine gave byte-identical `model.onnx` and `model_no_temp.onnx`, identical Lite rows
  (20) and identical run logs outside `time`. Its `metadata.json` differed in one thing only: on a busier machine the
  projection went over budget, so it applied pre-registered cut 1 (it dropped reference (d)). So `budget_cuts` and the
  two `regularised_gradient_boosting` records differ. The model bytes are reproducible; `metadata.json` is
  reproducible only when the budget rule takes the same branch, because that rule reads wall time. MindIR bytes
  differed between the runs, as pre-registered.

## Running this on Huawei Cloud

- `train_student` takes `--data_url <dir>` and `--train_url <dir>`, the names ModelArts uses (verified:
  https://support.huaweicloud.com/intl/en-us/develop-modelarts/develop-modelarts-0008.html and
  https://support.huaweicloud.com/intl/en-us/modelarts_faq/modelarts_05_0265.html). CPU, no local path in the code, no
  network call.
- On ModelArts: put the ward-v2-ext trial folders in an OBS bucket and make a custom training job with input
  `data_url` and output `train_url` on OBS folders. ModelArts copies the input into the container and uploads the
  output folder (verified, page 0008 above). The exact local folders (`/home/ma-user/modelarts/inputs/data_url_0/`)
  are unverified (https://support.huaweicloud.com/intl/en-us/docker-modelarts/develop-modelarts-0080.html returned
  404). Whether a preset image has MindSpore 2.11.0 is unverified; a custom image with the locked requirements and
  `libgomp1` would be needed.
- Free tier: new international accounts get free packages (verified, but old: page dated 2022-09-29,
  https://support.huaweicloud.com/intl/en-us/faq-billing/coupon_faq_00000032.html). The 12-month list for 2025-2026
  registrations and whether ModelArts is in it are unverified
  (https://activity.huaweicloud.com/intl/en-us/free_packages/index.html). No free ModelArts quota was found.
- MindSpore Lite 2.11.0 has a linux-aarch64 package with `converter_lite` and `benchmark` (verified:
  https://www.mindspore.cn/lite/docs/en/master/use/downloads.html).
- Nothing was run in the cloud. No account was made. Nothing was uploaded.

## Limits

- Circular and synthetic: the same simulator makes the data and the labels. Not validated. No claim about real
  patients.
- Seeds 261-300 both early-stop and select, so the validation numbers are optimistic.
- Held-out was scored once. Added to NEWS2, no gain beyond chance is shown there.
- "Consciousness not A" and the value-missing flags were constant 0 in training, so the student ignores them in
  practice.
- Missing-flag artefact: the chosen inputs include z-missing flags (set while the baseline forms) and 1-hour and
  4-hour change flags. These also mark the first hours of a stay.
- The soft targets are the teacher's logits on its own training rows (in-sample; teacher training loss 0.015943
  against validation 0.018699).
- `shortcut`: the image has no system libgomp; the venv links the copy shipped in the scikit-learn wheel. On a normal
  machine install `libgomp1`. The new lock was not re-audited with pip-audit.
- Not run on a small ARM board or a ward hub. Not wired to the API, the rank or the arbiter.

## Reproduce

Inside the research venv (`research/README.md`, ML-GATE; with `LD_LIBRARY_PATH=/venv/lib`), in a shell with brace
expansion:

    python -m research.train_student /data/trials/ward-v2-ext/seed-{61..160} /data/trials/ward-v2-ext/seed-{261..300} --lite_rows <dir> --run_log training/runs/student_vitals/<run_id>.json --commit <sha> --run_id <run_id>
    sh models/tier_c/student_vitals/convert_lite.sh <unpacked MindSpore Lite 2.11.0 dir> <model dir> <rows dir> | tee lite.txt
    python -m research.export_plain
    python -m research.evaluate_student <ward-v2 seed-11..30 folders> <ward-v2-ext seed-61..160 folders> --json models/tier_c/student_vitals/held_out.json > models/tier_c/student_vitals/held_out.txt

The last command was run once, on 2026-11-13. Do not run it again: it reads the held-out seeds.

Packages (exact, from `metadata.json`): Python 3.11.18, mindspore 2.11.0, numpy 2.4.6, onnx 1.23.2, onnxruntime
1.31.1, protobuf 7.36.2, pydantic 2.14.1, scikit-learn 1.9.1, scipy 1.17.1, threadpoolctl 3.8.0.

## References

- Hinton, Vinyals, Dean 2015. Distilling the Knowledge in a Neural Network. https://arxiv.org/abs/1503.02531
- Fakoor, Mueller, Erickson, Chaudhari, Smola 2020. Fast, Accurate, and Simple Models for Tabular Data via Augmented
  Distillation. NeurIPS. https://arxiv.org/abs/2006.14284 (hence reference c)
- van den Goorbergh, van Smeden, Timmerman, Van Calster 2022. The harm of class imbalance corrections for risk
  prediction models. JAMIA 29(9):1525-34. https://pmc.ncbi.nlm.nih.gov/articles/PMC9382395
- MindSpore 2.11.0 `mindspore.export`:
  https://www.mindspore.cn/docs/en/master/api_python/mindspore/mindspore.export.html; MindSpore Lite converter:
  https://www.mindspore.cn/lite/docs/en/master/converter/converter_tool.html (read 2026-11-13).
