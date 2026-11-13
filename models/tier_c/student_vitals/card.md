# Distilled vitals student (Tier C, research only)

| | |
|---|---|
| id | `tier_c/student_vitals` (variants `all` and `no_temp`) |
| files | `model.onnx` (5,334 bytes), `model_no_temp.onnx` (2,382), `model.mindir` (11,539), `model_no_temp.mindir` (8,706), `model.ms` (6,688), `model_no_temp.ms` (3,744), `model.json` (34,949), `model_no_temp.json` (14,903), `metadata.json`, `exports.json`, `timing.json`, `lite.txt`, `convert_lite.sh` |
| code | `research/student.py`, `research/train_student.py`, `research/evaluate_student.py`, `research/export_plain.py`, `research/plain_student.py` |
| status | trained on synthetic trials, validation numbers only, held-out not yet scored, not validated, not wired to the API, the rank or the arbiter |
| framework | MindSpore 2.11.0 (CPU, GRAPH_MODE, one thread) |
| intended use | research. Tier A (NEWS2) is never changed or learned |

Held-out not yet scored. Every number in this card is from training or validation seeds. No held-out number of this
model exists. The old scikit-learn student (B5) and its numbers are in git history.

## What the pitch line says and what is true now

- "A small neural network": true. One hidden ReLU layer. `all`: 35 inputs, width 32, 1,185 parameters (plus 70
  standardisation constants), `model.onnx` 5,334 bytes. `no_temp`: 27 inputs, width 16, 465 parameters (plus 54),
  2,382 bytes.
- "Trained inside the simulation": true. Simulator trials only: ward-v2-ext seeds 61-160 (100 trials) train, seeds
  261-300 (40 trials) validate. No real patient data.
- "Distilled from a much bigger model": true as a procedure, not as a gain. The student is fitted to the teacher's
  logits and never sees a label. The teacher is a 256x256 MindSpore network with 79,873 parameters, 67 times the
  student's (76,801 and 165 times for `no_temp`). It passed its pre-registered gate. BUT the distilled student's
  validation log loss (0.018969) is not better than the same small network fitted on the true labels (0.018925). The
  recorded difference, student minus best hard-label network, is +4.4e-05 (95% bootstrap -9.3e-05 to 0.000193);
  `no_temp` +4.6e-05 (-7.2e-05 to 0.000169). So distillation bought nothing measurable here. The rules ship the
  distilled student because the teacher gate passed, not because it won.
- "Every fifteen minutes it gives the probability that a patient is starting to deteriorate": true, with a definition.
  One probability per scored 15-minute bin. The label is positive from the true episode start to fully in. NOT RANKED
  bins get no probability.
- "Small enough to run on the ward hub, so it works without internet": partly shown. The files are small (above). One
  ONNX call with one row takes a median of 8.58 us (p99 26.63 us); `no_temp` 8.37 us (p99 29.75 us), onnxruntime
  1.31.1, one thread, on this Apple Silicon Docker host (aarch64). The MindSpore Lite `.ms` conversion was done and
  checked (below). A ward needs one call per patient per 15 minutes: about 13 calls (the validation trials average
  about 12.7 patients at a time). It was NOT run on a small ARM board or a ward hub. The per-patient feature
  computation is separate and was not timed.

## Result so far (validation only, seeds 261-300)

Held-out not yet scored. Whether the student adds anything to NEWS2 is NOT known yet. The previous model (B5,
scikit-learn, logistic) added nothing measurable on held-out, and nothing here shows the new one will.

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

The size of the gain, in plain words: the teacher's log loss is about 12% below the constant's, the student's about
11%. The student's AP is about 0.097 against a prevalence of 0.003. That is a real but weak signal: most bins above
any useful threshold are still negative.

## Data and split

- ward-v2-ext simulator trials (data card `models/data/ward-v2-ext/card.md`), 30 days each, scenario sha256
  `95052d02...98a8`. Synthetic only.
- Seeds 61-160 train the teacher, the students and the standardisation constants: 3,044,556 rows, 8,327 positive
  (0.2735%). Seeds 261-300 validate (early stopping, every model choice, the threshold): 1,207,464 rows, 3,795
  positive (0.3143%).
- Seeds 301-340 (the confidence check) and ward-v2 folders (seeds 1-60) are not read: the loader opens only their
  `manifest.json`. Held out: ward-v2 seeds 11-30, through `evaluate_student`, not run yet.

## Features, label

- Causal: bin t reads only bins up to t of the same stay. Never an input: time since admission, patient id, seed,
  anything from `episodes.csv`, `outcome`. Missing is the number 0 and a set flag; nothing is imputed.
- Base (19; 15 without temperature): per vital (heart rate, respiratory rate, SpO2, temperature) the value, a missing
  flag, z from the patient's own baseline, a z-missing flag; then the NEWS2 total, on oxygen, consciousness not A.
  `no_temp` drops the four temperature inputs and uses the NEWS2 total minus its temperature points.
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

The full grid (validation log loss / AP, parameters):

| Fit | `all` | `all` parameters | `no_temp` | `no_temp` parameters |
|---|---|---|---|---|
| teacher, decay 1e-4 | 0.018699 / 0.118078 | 79,873 | 0.018889 / 0.107075 | 76,801 |
| teacher, decay 1e-5 | 0.019006 / 0.109363 | 79,873 | 0.019024 / 0.106881 | 76,801 |
| distilled, base, 8 | 0.019492 / 0.060298 | 169 | 0.019596 / 0.052905 | 137 |
| distilled, base, 16 | 0.019464 / 0.064944 | 337 | 0.019586 / 0.058942 | 273 |
| distilled, base, 32 | 0.019453 / 0.06725 | 673 | 0.019568 / 0.062148 | 545 |
| distilled, trend, 8 | 0.019076 / 0.08398 | 297 | 0.019226 / 0.075691 | 233 |
| distilled, trend, 16 | 0.019011 / 0.091279 | 593 | 0.019154 / 0.087059 | 465 |
| distilled, trend, 32 | 0.018969 / 0.096846 | 1,185 | 0.019156 / 0.088939 | 929 |
| hard-label, base, 8 | 0.019302 / 0.062523 | 169 | 0.019533 / 0.054988 | 137 |
| hard-label, base, 16 | 0.019294 / 0.06596 | 337 | 0.019497 / 0.058773 | 273 |
| hard-label, base, 32 | 0.019314 / 0.068037 | 673 | 0.019498 / 0.064852 | 545 |
| hard-label, trend, 8 | 0.018925 / 0.09227 | 297 | 0.019108 / 0.086833 | 233 |
| hard-label, trend, 16 | 0.01893 / 0.097204 | 593 | 0.01915 / 0.090046 | 465 |
| hard-label, trend, 32 | 0.018971 / 0.099735 | 1,185 | 0.019159 / 0.092559 | 929 |

Per-epoch losses and wall times: `training/runs/student_vitals/m1-20261113-3d634f3.json`.

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
constants (largest mean difference from the training-row moments 1.4e-06). Its own refit of the teacher gate was NOT
finished: the gate numbers are the training run's own, reproduced only by the second training run below.

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
- Held-out not yet scored.
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
    python -m research.evaluate_student <ward-v2 seed-11..30 folders> <ward-v2-ext seed-61..160 folders>   # not run yet

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
