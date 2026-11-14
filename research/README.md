# research: offline evaluation of alerting rules (synthetic data only)

Standard-library Python plus an import of the backend's own `biosentry` package; the ML parts (slice
M1 and later) have their own `research/pyproject.toml` and `uv.lock`, and the standard-library parts
need neither. Nothing here is served by the API. This slice holds the yardstick: how NEWS2
alone performs as an alerting rule against the simulator's true deterioration episodes. Every
later model is compared with these numbers.

- `trials.py` reads one trial folder (format version 1: `manifest.json`, `cohort.csv`,
  `episodes.csv`, `summary.json`). It refuses an unknown format version, a missing file, a wrong
  header, a manifest without `synthetic: true`, and rows that break the contract. It opens only
  those four names, never follows a symlinked file, never evaluates content, and caps what it
  reads: 64 KiB per JSON file, 64 MiB per CSV, 1,000 characters per line, 1,000,000 cohort rows,
  100,000 episode rows.
- `rescore.py` re-scores every cohort row with the backend's NEWS2 and requires it to equal the
  sim's `ranked` and `news2` (a mismatch stops the run and names trial, patient and minute).
- `alerts.py` holds the policies and the alert rule; `events.py` the episode windows;
  `evaluate.py` the metrics and the command.

## Run

Trial data lives outside the repository. The real trials for this slice are
`~/Desktop/Projects/biosentry-data/trials/ward-v2/seed-01` to `seed-30` (30 simulated days each;
data card `models/data/ward-v2/card.md`). The earlier `ward-v1` set (sim commit 3a15e01, with the
fixed temperature rise) is kept beside it for comparison; its results are in the model cards.

    python -m research.evaluate <trial folder>...

It prints plain deterministic text (no clock, no paths) and exits 2 with one line on any refusal.
Trials must share format version, days, speed and scenario hash.

## The split

Seeds 1 to 10 are "tuning", 11 to 30 "held out". Metrics use the held-out seeds only. Tuning seeds
are loaded and parity-checked, then not scored here. `research.sweep` chooses the baseline's k and p on them. A seed outside
1 to 30 is refused. The student (slice M1) uses its own split, in its section.

## Definitions

- Event: the generator's true deterioration episode. It starts at `episode_start`, is fully in at
  `episode_start + ramp_minutes`, and its window runs from the start to fully-in + 120 minutes.
  Lead time is fully-in minus the FIRST new alert in the window (positive: before fully in).
- Three detection measures per policy, printed side by side and not interchangeable:
  (a) NEW alert: an alert fires inside the window. (b) ALERTING in any bin: the policy is alerting
  in at least one bin of the window. (c) the count with (b) but not (a): the alert was already open
  before the window. (a) is not monotone in the policy: a policy that alerts earlier and more can
  score fewer new alerts, because an alert opened before the window stays open and cannot fire again.
- Dropped: episodes of the 12 starting patients (admit minute 0; true arrival unknown, and one
  has a scripted worsening that is not an episode), episodes starting after the trial, and
  episodes starting outside the patient's stay. Starting patients stay in the alert-burden counts.
  Kept episodes whose window is cut short by the end of the stay or trial are counted; sensitivity
  is printed for all evaluated episodes and for episodes not cut short, and the episodes whose stay
  or trial ended before they were fully in are counted separately. A kept episode with no cohort
  rows at all is counted on its own line and is not detected.
- Chance control (no random draws): each evaluated episode's window (same offset from admission,
  same length) is laid on every admitted, non-starting patient of the same trial whose stay covers
  the whole window and in whose stay no episode begins (no episode, or one starting after the stay
  or trial ended). The shares of control windows with a new alert and with an alerting bin are the
  chance rates; "excess over chance" is sensitivity minus that. Control windows are never cut
  short, so the like-for-like comparison is the "not cut short" sensitivity. The chance rates have
  no interval.
- Alert: one open alert per patient; it fires when the policy turns from not alerting to
  alerting and re-arms after 16 bins (4 hours) not alerting (a judgement, the same for every
  policy). A missing bin or a NOT RANKED row is not alerting.
- Burden: alerts per patient per 12-hour shift and per 100 patient-days (patient-days from each
  `summary.json`), and time in alert (cohort rows alerting / cohort rows).
- Abstention: NOT RANKED rows over cohort rows; bins with no row over the bins expected in the
  stays (every 15-minute boundary from admission to just before leaving); and the two together.
  The episodes whose start fell in a NOT RANKED or missing bin (the bin ending at or after the start)
  are counted.
- Policies: `news2_chart_trigger` is the product's arbiter itself (`arbitrate(...).escalated`:
  total 5 or more, or any single parameter at 3); `news2_total_ge_5` is total 5 or more only.
  Add a policy by adding a `Policy` to `alerts.POLICIES`.
- Intervals: patient-level bootstrap (each patient of the held-out trials, with replacement), 1,000
  resamples, a SplitMix64 generator (`bootstrap.py`, fixed seed 20261112, no `random` module),
  2.5th to 97.5th percentile, for the alert rate and for the two sensitivities. Exposure per
  patient is the stay inside the trial.

## Baseline policies and the sweep (slice B4)

Two policies use each patient's own baseline (`baseline_policy.py`, which calls the backend's
`baseline_finding` on the bins up to each bin, so a bin never sees the future): "baseline alone"
(any of heart rate, respiratory rate, SpO2, temperature is significant) and "NEWS2 chart trigger
OR baseline" (either one alerts). The alert rule, windows, chance control and bootstrap are the
ones above.

How a bin is treated, as decided by the detector itself and not by us: before a patient has 24
valid bins there is no finding (baseline forming) and the bin is not alerting. A bin with no
cohort row has no state and is not alerting. A row whose value for a vital is missing gives no
finding for that vital (latest bin missing), and a gap ends a run; nothing is filled in. A NOT
RANKED row (NEWS2 abstains) is still judged on the vitals it has. The "abstention" lines for the
two new policies repeat NEWS2's NOT RANKED count; the detector has no abstention count of its own.

Choosing k and p (`python -m research.sweep <trial folders>`). Written before the sweep was run:

- Grid: k in 1.5, 2.0, 2.5, 3.0, 3.5, 4.0 and p in 2, 3, 4, 6, 8. Window and floors stay at the
  detector's defaults.
- Only tuning seeds 1 to 10 are scored. Held-out trials in the folder list are skipped and are
  never part of any number.
- Budget: a setting qualifies when "NEWS2 OR baseline" raises at most 25% more alerts per patient
  per 12 hours than the NEWS2 chart trigger alone, on the tuning seeds.
- Among qualifying settings, pick the largest excess over chance of ALERTING in any bin, on
  episodes not cut short, for "NEWS2 OR baseline" (sensitivity minus the control-window rate).
- Ties go to fewer alerts, then larger k, then larger p.
- If no setting qualifies, nothing is chosen. That is a valid result.

The command writes nothing unless `--write-params <path>` is given. `evaluate` then adds the two
rows for the chosen k and p, read from `models/tier_b/baseline/params.json` when it exists,
otherwise the detector's DEFAULTS (it prints which).

## The distilled vitals student (slice M1; replaces B5)

Written before any M1 training run. Nothing here changes after a validation number has been seen.
Synthetic, not validated: the data are the seeded ward simulator only.

What B5 was: one gradient-boosted teacher, a ridge and a 16-unit student, on seeds 1-10 with 3
validation trials. It could not show that the student earned its teacher, and its no-temperature
rows were stuck open. M1 replaces it. Both networks are now MindSpore networks, because the project
is entered in a Huawei ICT competition. scikit-learn stays only for two comparison rows.

Goal: a small neural network, trained inside the simulation and distilled from a much bigger model,
that every 15 minutes gives the probability that a patient is starting to deteriorate, small enough
to run on the ward hub, offline. If the evidence does not support "distilled", the fallbacks below
say what ships instead.

Code: `student.py` (split, features, networks, selection, exports), `train_student.py` and
`evaluate_student.py` (the commands). They need numpy, MindSpore 2.11.0, scikit-learn, onnx and
onnxruntime (`research/pyproject.toml`, `uv.lock`); nothing else in `research/` imports them.

### Data and split

- Data: `ward-v2-ext` (data card `models/data/ward-v2-ext/card.md`), made by the same generator as
  ward-v2. The loader refuses a trial whose days, speed or scenario sha256 differ from ward-v2's
  (30, 60, `95052d02...98a8`).
- Seeds 61-160 train (teacher, students, standardisation constants). Seeds 261-300 validate (early
  stopping, every model choice, the threshold). Seeds 301-340 are reserved for conformal
  calibration: the loader opens only `manifest.json` and skips them. ward-v2 folders (seeds 1-60)
  are skipped the same way. Any other seed is refused (161-260 are read by no slice).
- Held out: ward-v2 seeds 11-30, scored once by `evaluate_student` (by procedure; it was run once, on 2026-11-13, and its outputs are
  `models/tier_c/student_vitals/held_out.txt` and `held_out.json`). It refuses ward-v2 seeds 1-10,
  which the student no longer uses. The training loader opens only `manifest.json` of a ward-v2 folder and skips it. ward-v2-train (31-60)
  is the reward loop's data.
- 100 training trials (about 3 million scored bins) were chosen over 200 because of memory (8 GB
  shared). Validation is never cut. If memory fails, only float32 and chunking may change.

### Bins, labels, features

- One feature row per scored bin, from that bin and earlier bins of the same stay only. A bin is
  positive from `episode_start` to fully in (`episode_start` plus `ramp_minutes`); bins after fully
  in are excluded; starting patients (admit minute 0) are left out; `outcome` is never a label.
  NOT RANKED bins are not scored and not trained on, but their values still feed lags, windows and
  the baseline.
- Base inputs (19; 15 without temperature), in this order. Per vital (heart rate, respiratory
  rate, SpO2, temperature): the value; a missing flag; z from the patient's own baseline
  (`baseline_finding`, the detector's DEFAULTS, bins up to this one); a z-missing flag (set while
  the baseline forms, fewer than 24 valid bins). Then the NEWS2 total, on oxygen, consciousness not
  A. Missing is missing: the number 0 and its flag; nothing is imputed. In the training trials the input "consciousness not A" and the
  value-missing flags were constant 0 (every scored bin is alert and has all four device vitals), so their standardised value is 0 and
  the student ignores them in practice. The no-temperature set drops
  the four temperature inputs and uses the NEWS2 total minus its temperature points.
- Trend inputs (student option; 16 more, 12 without temperature): per vital d1h = value(t) -
  value(t - 60 min) and d4h = value(t) - value(t - 240 min). The lag bin is any cohort row at that
  minute with a value, RANKED or not. A missing lag gives 0 and a set flag. 35 inputs in all (27).
- Teacher inputs (53; 41): the base inputs, then per vital d1h, d4h, the mean of valid values over
  the last 16 bins, and the least-squares slope per hour over the same 16 bins (at least 4 valid
  points), each with a missing flag; then the change in the NEWS2 total over one hour with a flag
  (without temperature: the total minus its temperature points).
- Never an input: time since admission, patient id, seed, anything from `episodes.csv`, `outcome`.
  Known artefact: the missing flags also mark the first hours of a stay.
- Inputs are standardised with the training rows' mean and standard deviation (a standard
  deviation of 0 becomes 1). The students carry these constants inside the graph.

### The teacher (per variant: all, no_temp)

- A MindSpore MLP on the teacher inputs: n-256-256-1, ReLU, no dropout (dropout breaks
  bit-identity above one thread). 79,873 parameters with 53 inputs, 76,801 with 41. It outputs a
  logit; the sigmoid is applied only in the loss and at scoring.
- Loss `nn.BCEWithLogitsLoss` on the true labels. No class reweighting and no resampling (van den
  Goorbergh 2022). The last bias starts at the training log-odds of prevalence; other weights use
  MindSpore's `nn.Dense` defaults.
- `nn.Adam`, learning rate 1e-3, weight decay on the weight matrices only (not the biases), batch
  1024, at most 15 epochs. Rows are shuffled each epoch with `numpy.random.default_rng(0)`; a last
  partial batch is dropped each epoch (shortcut: a different set of rows is dropped each epoch, at most
  1,023 of about 3 million), and a batch larger than the row count becomes the row count
  (`min(batch, rows)`, which only matters in tests). `mindspore.set_seed(0)` runs before each network is built. GRAPH_MODE,
  CPU, one kernel thread.
- Early stopping: after each epoch the validation log loss against the true labels of seeds
  261-300. Stop after 3 epochs without a strictly lower value. The best epoch's weights are kept (a
  tie keeps the earlier epoch).
- Soft targets for the students: the chosen teacher's logits on the training rows, clipped to
  +-9.2102 (p between 1e-4 and 1-1e-4). This clip is used for the students' soft targets and for the recorded fidelity RMSE only.

### The student (per variant)

- One hidden ReLU layer of width w on base or trend inputs. Parameters w(n+2)+1: base 21w+1, trend
  37w+1; without temperature base 17w+1, trend 29w+1. Largest: 1,185 (all) and 929 (no_temp). The
  standardisation constants are counted separately (2n). The teacher has 67 and 83 times more
  parameters than the largest student.
- Loss: mean squared error on the teacher's clipped logits. No true label is used. Matching logits
  is the high-temperature limit of Hinton's soft-target loss, so a distillation temperature would
  only rescale the target. A probability-space target gives almost no gradient on negative bins at
  about 0.3% of scored bins positive (0.2735% in training, 0.3143% on validation). The last bias starts at the mean target logit.
- `nn.Adam`, learning rate 1e-3, weight decay 1e-5 on weights only, batch 4096, at most 8 epochs,
  early stopping on the validation log loss against the true labels, patience 2, best epoch kept.
  Seeds as for the teacher.
- The exported cell: Sub(mean), Div(std), Dense, ReLU, Dense, Sigmoid. Input `x` float32 [1, n],
  output `p` float32 [1, 1].

### References and the teacher gate

All are fitted on the same training rows and scored on validation seeds 261-300.

- (a) a constant at the training prevalence.
- (b) the old recipe, the baseline row: `HistGradientBoostingClassifier(max_iter=500,
  early_stopping=True, n_iter_no_change=20, scoring="loss", random_state=0)` on the base inputs,
  inside `threadpool_limits(4)`.
- (c) the student's own class on HARD labels, in MindSpore: the same 6 width x input
  configurations, the same optimiser, batch, epochs and early stopping, loss BCEWithLogits on the
  true labels, last bias at the prevalence log-odds. The best of the 6 is used.
- (d) recorded only, never gating: one regularised gradient boosting on the teacher inputs
  (learning rate 0.05, l2 1.0, `max_leaf_nodes` 15, `min_samples_leaf` 2000, early stopping 50 on
  validation, then a refit at the best iteration). The card says if the MindSpore teacher is worse
  than (d).
- Teacher gate, per variant: the teacher's validation log loss is below (a), (b) and (c), and it has
  at least 10 times the student's parameters. "Below" means the 97.5th percentile of the paired
  difference (teacher minus reference) is under 0. The difference comes from a patient-level
  bootstrap of the validation patients: the repo's SplitMix64, 1,000 resamples, seed 20261112
  (`paired_gate`).
- Recorded, not gating: the student's log loss minus the best (c), with its interval.

### Grid, metric, tie-breaks (per variant)

- Teacher: weight decay {1e-5, 1e-4}: 2 fits. The width is fixed at 256x256 for the time budget.
- Student: width {8, 16, 32} x inputs {base, trend}: 6 distilled fits. The same 6 on hard labels
  are reference (c).
- Primary metric for every choice: validation log loss against the true labels of seeds 261-300.
  Every compared, selected, gated or recorded log loss (teacher, students, references a to d, the ONNX
  value) comes from one function, `row_loss`, applied to the model's own unclipped probability with
  the same 1e-7 clip. Ties are compared on unrounded values; rounding to 6 decimals happens only when
  the metadata is written.
- Ties: values within 1e-6 count as tied. Then fewer parameters, then base over trend inputs, then
  the larger weight decay.
- Recorded, never used to choose: average precision, mean p against prevalence, fidelity (RMSE of
  the student's logits against the teacher's on validation, and student minus teacher log loss),
  the best epoch of every fit, and every epoch's validation loss.

### Threshold (the student, the teacher and the two no-temperature models)

- The alert rule above with "alerting when p >= t", t from 0.001 to 0.009 (steps of 0.001) and 0.01
  to 0.99 (steps of 0.01), on seeds 261-300. A threshold qualifies only if BOTH its alerts and its
  time in alert (alerting bins over cohort bins) are at most the NEWS2 chart trigger's on the same
  seeds. The smallest qualifying t is taken. If none qualifies, the row is "no usable threshold"
  and is not scored. Counting alerts alone is not enough: one alert that stays open looks like a
  small burden (B5's no-temperature models: 52.7% time in alert against 12.4%). "NEWS2 OR student"
  uses the student's threshold. The shipped artefact is the ONNX file, so the STUDENT's threshold and
  its validation alerts and time in alert are computed from the ONNX output through onnxruntime. The
  teacher's threshold is computed on MindSpore's probabilities. Model selection (every validation log
  loss above) stays on MindSpore's probabilities; `metadata.json` also records the chosen student's
  validation log loss recomputed from the ONNX output (`val_log_loss_onnx`).

### What ships, and what each tool writes

- Per variant, `distilled_student` ships if the teacher gate passes and the student beats the
  constant (same bootstrap rule). Otherwise fallback F1: the best hard-label MindSpore network from
  (c) ships if it beats the constant, with `distilled: false`; the card's first line names the
  failed comparison and its numbers, and "distilled from a much bigger model" is then false. F2:
  if no MindSpore network beats the constant, that variant is not exported, and the card says so.
  Any old model file of such a variant is removed.
- `model.mindir`, `model_no_temp.mindir`: written by MindSpore 2.11.0 `mindspore.export` from the
  trained network.
- `model.onnx`, `model_no_temp.onnx`: written by `onnx.helper` from the same MindSpore-trained
  float32 weights (opset 13, IR 7, ops Sub, Div, Gemm, Relu, Sigmoid, `x` [1, n] to `p` [1, 1]).
  MindSpore's own ONNX export gave IR 4 and opset 11 in a probe, so it is not used.
- `model.ms`, `model_no_temp.ms` (only if the hand-run Lite step is done): written by MindSpore Lite
  2.11.0 `converter_lite` from the `.mindir`. Research code starts no external program (no subprocess, no shell; it does use Python's own
  worker processes for feature extraction), so this is a separate step: `models/tier_c/student_vitals/convert_lite.sh` (see Commands). Its raw output is
  committed as `models/tier_c/student_vitals/lite.txt` when it is run.
- Parity on 1,000 fixed training rows: the largest p difference is at most 1e-5 for each pair of
  MindSpore forward, MindIR loaded back with `nn.GraphCell`, and ONNX in onnxruntime. The hand-run
  Lite step does the same for `.ms` on 20 of those rows (1 thread).
- `metadata.json` (byte-reproducible): the split, seeds read and skipped, feature names, the full
  grid with every validation number, references (a)-(d), the gate, parameter counts and their
  ratio, thresholds with their alerts and time in alert, `framework: "MindSpore"`,
  `framework_version`, every package version, the thread count, the mode, the seeds, the weight
  sha256, the ONNX sha256 and size, the parity numbers, `byte_reproducible` and
  the budget cuts applied. No wall time and no path is in it.
- `exports.json`: MindIR sizes and sha256, and a plain note that the Lite step is made by hand.
  `timing.json` (not compared): onnxruntime single-row timing (1 thread, 1,000 warm-up calls, 10,000
  timed calls, median and p99, `platform.machine()`), and the run's wall time. Wall time is measured with `timeit.default_timer`
  (`research/` may not import `time`).

### Reproducibility

- Promised, in `biosentry-api-build` on linux aarch64 with one kernel thread only: two full
  training runs give identical weight bytes, identical ONNX bytes and identical `metadata.json` bytes. MindIR bytes are NOT promised (a timestamp inside); MindIR is
  checked by sha256 against `exports.json` and by identical outputs on the parity rows.
- `evaluate_student` refits only the chosen teacher and student, each for exactly its recorded best
  epoch count (the weights after epoch k of a monitored fit are those of a k-epoch fit, so no
  validation row is read), and refuses on any mismatch of the weight sha256 or the ONNX sha256 with
  `metadata.json` or the committed file. It first refuses if the sha256 of `metadata.json` differs from the one in `exports.json` (the file changed after training). It also checks the MindIR files against `exports.json`.
- Pre-registered fallback if the two runs differ in bytes: `metadata.json` carries
  `byte_reproducible: false` (set with `--byte_reproducible false`), no claim of byte
  reproducibility is made, and the card states the measured largest p difference between the two
  runs on validation. `evaluate_student` then checks the committed files by sha256 only, refits only
  the teacher (without a bytes check), and re-scores the committed ONNX on validation seeds 261-300,
  requiring its log loss within 1e-6 of `metadata.json`. In that mode it also needs the
  ward-v2-ext folders of seeds 261-300.

### Budget rule (decided before any metric)

- Projected, one process, one thread: teachers 4 fits x up to 15 epochs x 65-130 s; students 24
  fits, about 0.5 h; references (b) and (d), about 0.5 h; features 0.2 h (cached afterwards). About
  2.4 h central, 4.5 h worst case. Peak memory 3-4 GB.
- After the first teacher's first epoch the total is projected: the number of teacher fits x the
  epoch cap x that epoch's seconds, plus 4,320 s for everything else (1,800 students, 900 reference
  (b), 900 reference (d), 720 features). The first epoch is timed whole, with its validation pass, and includes graph compilation, so this is a
  slight overestimate. If it exceeds 3 h, these cuts are applied in order until it does not: (1) drop
  reference (d); (2) teacher epoch cap 15 to 10; (3) teacher weight decay grid to the larger value
  only. The cuts applied are recorded in `metadata.json`. Wall time is not a metric.

### Stop rule and Park

- At most 3 build and evaluate rounds; a round fixes bugs or contract violations only. The grid,
  the metric, the thresholds and the rules never change after a validation number has been seen.
  If a bug forces a second held-out run, both runs are reported. After 3 rounds without a pass,
  nothing is merged and main keeps B5.
- If MindSpore cannot run in the gate (for example the libgomp fix stops working), stop and report.
  Do not fall back to scikit-learn as the shipped model.

### Commands

Run inside the research venv (see the ML gate below). Give only seeds 61-160 and 261-340 (301-340
and ward-v2 folders are skipped after reading their manifest; other seeds are refused):

    python -m research.train_student <ward-v2-ext folders> [--out <dir>] [--cache <dir>] [--workers N] [--lite_rows <dir>] [--byte_reproducible true|false] [--run_log <file>] [--commit <sha>] [--run_id <text>]
    python -m research.train_student --data_url <dir of trial folders> --train_url <dir> [--cache <dir>] [--lite_rows <dir>]
    python -m research.evaluate_student <ward-v2 seed-11..30 folders> <ward-v2-ext seed-61..160 folders> [--cache <dir>] [--json <path>]

- `--run_log <file>` (optional) writes one small JSON run log (created with its parent folders):
  `model`, `run_id`, `commit`, the framework and package versions, the settings, the data (seed
  lists, row and positive counts, no path), then for every fit of both variants (2 teachers, 6
  distilled students, 6 hard-label students) its inputs, width, weight decay, parameter count, the
  mean training loss and the validation log loss of every epoch, the best epoch, the final validation
  log loss and average precision, plus references (b) and (d) with their iterations, the gate
  numbers, the chosen models, thresholds and validation burden, and the budget cuts. Every wall-clock
  number is under one `time` key, so two runs give the same bytes once `time` is removed. The log is
  separate from `metadata.json` and is not hashed by `evaluate_student`. `--commit` and `--run_id`
  are recorded as given: `--run_id` takes letters, digits, `.`, `_`, `-`, at most 64 characters; `--commit` takes printable text up to 128
  characters. Research code cannot call git. `evaluate_student` writes its text and its `--json` file in the same single read.
- `--out` defaults to `models/tier_c/student_vitals/`. `--cache` keeps per-trial features in a
  folder outside the repo, keyed by the sha256 of the trial's four files and a feature-code
  version. Progress lines (epoch number and seconds only) are printed as it runs; validation
  numbers appear only in the final summary and in `metadata.json`.
- `--data_url` and `--train_url` are the argument names Huawei ModelArts uses for a custom training
  job: every sub-folder of `data_url` (sorted) is a trial and `train_url` is the output folder, the
  same as `--out`. The code runs on CPU, makes no network call and names no local path. On
  ModelArts: put the ward-v2-ext trial folders in an OBS bucket, make a custom training job with
  input `data_url` and output `train_url` pointing at OBS folders. A custom image with the locked
  requirements and `libgomp1` would be needed. Nothing was run in the cloud, no account was made
  and nothing was uploaded.
- `--lite_rows <dir>` (optional) only writes files: for each exported variant, 20 fixed training rows
  as float32 input files (`row<i>.bin`) and the calibration files the MindSpore Lite `benchmark`
  expects (`row<i>.txt`, MindSpore's p), into `<dir>/<model stem>/`.
- The Lite conversion is a separate, hand-run step, because research code starts no external program (no subprocess, no shell; it does use Python's own worker processes for feature extraction; the one exception is the question worker's model mode, which starts one Python child for the model, `research/agent_tuning/route.py`):

      sh models/tier_c/student_vitals/convert_lite.sh <unpacked MindSpore Lite 2.11.0 dir> <model dir> <rows dir> | tee lite.txt

  The package is fetched by hand from the official site (its tarball sha256 was
  `886186849c3291933b7265ebaa0d51df2bb175e7cbb32b488795ea19a96b2d6c` when read). The script never
  downloads anything and uses no network. For each `*.mindir` it runs `converter_lite
  --fmk=MINDIR`, prints the converter's result line and the `.ms` file's bytes and sha256, then runs
  the Lite `benchmark` with one thread on each of the 20 rows and prints "rows within 1e-5: N of
  20". The benchmark's bias is relative, so a row passes when its bias is under 100 x 1e-5 / p
  percent. It exits non-zero on any failure. Its raw output is committed as
  `models/tier_c/student_vitals/lite.txt` when it is run. `shortcut`: the output tensor's name is
  found in the `.ms` file (the last Sigmoid node).
- `evaluate_student` runs the teacher with its MindSpore network and the student rows through the
  committed ONNX in onnxruntime, next to NEWS2 and the B4 rows: teacher, student, NEWS2 OR student,
  and the three no-temperature rows. A model with no usable threshold has no row, and the header
  says why.

Limits: the data are circular (the same simulator makes the data and judges the alerts); seeds
261-300 both stop and choose, so validation numbers are optimistic; the missing flags mark the
start of a stay; the models were not run on a small ARM board or a
ward hub; timing is for this Apple Silicon Docker host. The 2026-11-12 pip-audit predates
MindSpore, and the new lock has not been audited.

References: Hinton, Vinyals, Dean 2015 (arXiv 1503.02531), soft targets and logit matching as the
high-temperature limit; Fakoor et al. 2020, NeurIPS (arXiv 2006.14284), where the distilled model
is compared with one trained on the raw labels, hence reference (c); van den Goorbergh et al. 2022,
JAMIA 29(9) (no reweighting); MindSpore 2.11.0 `mindspore.export`, MindSpore Lite 2.11.0
`converter_lite` and the ModelArts custom training pages (read 2026-11-13).

## The reward-driven alerting policy (slice M2; replaces B6's loop)

Written before any M2 run. Code: `reward_policy.py` (the policy and the reward) and `reward_loop.py`
(the loop, the outer loop and the held-out command). Standard library only; the tests are in
`tests/test_reward.py` and `tests/test_reward_loop.py`. It is a (1+30) evolution strategy with a
reward: black-box search, not deep or per-step reinforcement learning. Synthetic, not validated.

The B6 loop is kept as `research/reward_loop_b6.py` for the research agent, reached through
`python -m research.reward_loop --train ... --validate ...`. It is not the loop the card reports.

- Policy: six numbers, four non-negative weights (heart rate, respiratory rate, SpO2,
  temperature), a threshold theta and a persistence p (integer 1 to 8). Per bin, for each vital,
  z from the patient's own baseline (`baseline_finding` with the detector's DEFAULTS window and
  floors, called on the bins up to this one, as in B4 and B5), clipped at 0 in the bad direction.
  Score = the weighted sum (form `sum`) or the largest single weighted z (form `max`).
  Alerting when the score is at least theta in p consecutive scored bins. A NOT RANKED bin is not
  scored: it is not alerting and ends a run (abstention is an output). A bin with no row ends a
  run. A vital whose value is missing, or whose baseline is still forming, contributes nothing;
  nothing is filled in. Causal: a bin reads itself and earlier bins only.
- Generation 0 is the B4 rule's equivalent: weights 1, 1, 1, 1, theta 3.0, p 4. It is a superset
  of B4, which needs the same vital past k for p bins in a row. With form `sum`, several moderate
  vitals together also fire. With form `max`, any one vital clearing theta in each of the p bins
  fires, possibly a different vital in each bin. Unlike B4 it never alerts on a NOT RANKED bin.
- Reward for one trial (unchanged since B6): C - lambda * S * a. C = evaluated episodes with the
  policy alerting in a bin from `episode_start` to fully in. a = the policy's alerts per patient
  per 12 h. S = E / a_news2, so the penalty is lambda * E * (a / a_news2) and both terms are in
  episodes. lambda = 0.5, fixed, never tuned. Hard rule: time in alert at most the NEWS2 chart
  trigger's on the same trial; a policy over it on any trial of a set is rejected on that set.
  `outcome`/higher care is never part of the reward. A set is scored by the mean reward. NEWS2 is
  never learned. A trial with no evaluated episode scores 0 for every policy; the time-in-alert rule
  still applies. Added 2026-11-13 after round 1, when seed 509 was the first such trial; round 1
  had none, so its files are unchanged.
- Data: all trials are in `ward-v2-ext`. Validation is seeds 341-400 (60 trials, about 270
  episodes). Generation seeds start at 401, strictly increasing, each used once in the
  with-temperature schedule. Generation g of round 1 takes selection seeds 401+20(g-1) to
  408+20(g-1), then confirmation seeds 409+20(g-1) to 420+20(g-1). Round R+1 starts at the first
  seed after round R's last generation. Budget: generation seeds 401-2800; a round starts only if
  a full 40-generation round still fits (rounds 1-3 always do; round 4 only if rounds 1-3 used at
  most 1,600 seeds). Seeds 61-340 belong to other slices and ward-v2 seeds 11-30 are held out:
  the loaders refuse them, and a manifest that names another seed or a trial whose days, speed
  or scenario hash differ from the validation trials', before any file but `manifest.json` is
  opened. Before a generation loads anything, every folder of its 20 seeds must have
  `manifest.json` and `summary.json`; if not, nothing is written, `needs seeds a to b` is printed
  on stderr (that generation's whole range) and the exit code is 3.
- One generation, with the current policy c:
  1. Draw 30 candidates around c: SplitMix64 seeded 20261112 + 1000 x R + g (R the round, g the
     generation), Box-Muller normals, weights floored at 0, theta at 0.1, p rounded and clipped
     to 1-8, values rounded to 4 decimals.
  2. Selection: score c and all 30 on the 8 selection seeds. Drop any candidate rejected for time
     in alert on any of them. The winner has the highest mean reward (ties: first drawn). If no
     candidate is left, or the winner's mean is not strictly above c's on the same seeds, the
     generation accepts nothing. Selection numbers are never used to accept: the best of 30 is
     biased upward (winner's curse).
  3. Confirmation: score the winner and c on 12 fresh seeds. d_i = winner minus c on seed i.
     Accept if and only if the winner is not rejected for time in alert on any of the 12 and
     mean(d) > 1.796 x stdev(d) / sqrt(12) (stdev with n - 1; strict; 1.796 is the one-sided 5%
     t value for 11 degrees of freedom). If stdev(d) is 0 the rule is mean(d) > 0. c's own
     rejection status does not block or force acceptance; it is recorded.
  4. A winner that fails step 3 is not replaced by the second best: that would be a second look
     at the same confirmation batch. The generation accepts nothing.
- Step size sigma: 0.5 for weights and theta, 1.0 for p, at the start of every round. After a
  generation that accepts nothing, multiply by 0.85; after an accepted one keep it (a crude
  success rule, Beyer 2000). Floors: 0.1 for weights and theta, 0.25 for p.
- Validation and the fixed stop rule: after generation 0 and after every generation, the current
  policy is scored on seeds 341-400. Its validation reward is the mean, or "rejected" if it
  breaks the time-in-alert rule on any of the 60. "Improved" means not rejected and strictly above
  the best validation reward so far in this round (generation 0 counts; if it was rejected, any
  non-rejected value improves). A generation that accepts nothing has the same policy, so it is not
  improved. The patience counter is 0 after generation 0 and after an improving generation, and
  plus 1 otherwise. The round stops when it reaches 3, or after generation 40, whichever is
  first. The round ships the generation that last improved (the highest validation reward, ties:
  the earlier generation). If no generation is non-rejected, the round ships nothing and is
  dropped.
- Sizes and the power note. Pilot: ward-v2-train seeds 31-60 only (old training data), 30 trials,
  134 evaluated episodes. The SD of one trial's reward is about 1.1; the SD of the paired
  difference between two nearby policies is about 0.75 (design value; median 0.74 for sigma 0.5
  mutations, 0.56 for sigma 0.25). The 8-trial selection batch has a paired SE of 0.27, below the
  0.35 spread of true differences among mutations, so the ranking carries some signal. The
  12-trial confirmation has an SE of 0.22 and a margin of 0.39 reward units (8.7 more episodes
  caught early per 100 at equal alerts). False accept per generation with no true difference: 5%
  by design (4.6% on resampled pilot differences); only one candidate is tested, so it does not
  grow with the 30 draws; at most about 2 over 40 generations. Power: true gain 0.3 gives 45%, 0.5
  gives 76%, 0.75 gives 95%. Validation on 60 trials: one policy's mean has SE 0.14, a paired
  difference 0.097; the smallest difference it can tell from noise is about 0.16 (one-sided, 5%).
  Today's B6 loop always accepted because on 3 trials the paired SE (0.43) made the best of 30
  sit about 0.9 above its true value.
- Search configuration (six keys, the defaults first). A round changes exactly one key of the
  incumbent's:

  | Key | Values |
  |---|---|
  | `sigma0` | 0.5 (p 1.0); 0.25 (p 0.5); 1.0 (p 2.0) |
  | `shrink` | 0.85; 0.7 |
  | `selection_trials` | 8; 16 (8 more seeds per generation) |
  | `form` | `sum`; `max` |
  | `fix_theta` | false; true (theta not mutated) |
  | `frozen_vital` | none; heart_rate, respiratory_rate, spo2 or temperature (weight fixed at 0) |

  The reward, lambda, the time-in-alert rule, the 30 candidates, the stop rule, the validation
  seeds and the held-out split are not keys and never change. A frozen weight is set to 0 in the
  round's generation 0 as well.
- Outer loop. A round is one finished inner run with one configuration, with temperature. Round 1
  is generation 0 with the default configuration and is the first incumbent ("baseline"). Rounds
  2-4 start from the incumbent's shipped params, on fresh seeds, with sigma reset, and change
  exactly one key; the command refuses any other change. After a round, a research agent reads
  that round's generation records and the ledger, then writes exactly one change and why (at most
  600 characters, naming the generation records it relies on) or "no change proposed: stop".
  Kept rule: let v_i be the per-seed difference on seeds 341-400 between the round's shipped policy
  and the incumbent. Keep if the round's policy is not rejected and mean(v) > 2 x stdev(v) /
  sqrt(60). Otherwise drop; the incumbent stays. Why 2 SE: validation is reused. It picks the
  shipped generation in every round and judges up to 4 rounds, so its numbers are optimistic
  (Dwork et al. 2015); 2 SE is a guard, not a test with an exact level. Held-out stays honest
  because nothing reads it before the final command.
- Ledger: `experiments.jsonl`, one JSON object per line, sorted keys, each carrying
  `prev_line_sha256` (sha256 of the previous line; "none" for the first). Round lines: `round`,
  `started_from` (round, policy sha256, validation reward), `change` (key, from, to), `why`,
  `config_sha256`, `seeds` (first, last), `generations`, `stop_reason`, `shipped` (generation,
  params, validation reward, policy sha256), `vs_incumbent` (mean, stdev, margin), `decision`
  (`baseline`, `kept` or `dropped`). The outer loop ends with `{"decision": "stop", "reason": ...}`
  (no change proposed, 4 rounds done, or budget).
- No temperature: after the stop line, the no-temperature policy is learned once by the same inner
  loop from generation 0 (weights 1, 1, 1, 0), with the finally kept configuration and the
  temperature weight fixed at exactly 0. It shares the seed schedule from 401, uses round number 1
  for its generator seeds, has no outer loop, and adds one `no_temperature` ledger record.
- Files (same inputs, same bytes): `models/tier_b/reward_policy/rounds/round-R/gen-NN.json` and
  `round-R/policy.json`; the incumbent's policy as `policy.json` at the top;
  `no_temperature/generations/gen-NN.json` and `no_temperature/policy.json`. Every generation
  record (schema 2) holds the round, the configuration and its sha256, the seeds, sigma used and
  next, the selection summary, the confirmation (per-seed d_i, mean, stdev, threshold, reason),
  the params after, the validation (mean and per seed), best, patience, `stopped`, `prev_sha256`
  (sha256 of the previous file's bytes; for generation 0 the starting policy file's bytes, or
  "none") and its own `sha256`. Resume: on start the command reads `gen-00` to the last file and
  refuses (exit 2) a missing number, a body that does not match its `sha256`, a broken link, a
  different configuration or temperature flag, seeds that are not the schedule, or a file after a
  stopped record; a stopped record rewrites `policy.json` and exits 0; otherwise it continues
  (each generation seeds its own generator, so no RNG state is carried). A stray `.tmp` is
  deleted. The hash chain catches accidents and half-written files, not a forger; the pull request
  review is the guard. `--jobs N` parallelises preparing trials and scoring with the standard
  library (fork start method; serial elsewhere); results are collected and summed in seed order,
  so the bytes do not depend on N. The 60 validation trials stay in memory for the run.
  shortcut: resuming prepares the validation trials again (minutes), fine for 4 rounds.
- Held-out, once: `--evaluate` goes through `research.evaluate` (same windows, chance control,
  bootstrap), refuses a `policy.json` whose sha256 does not match, and builds each policy from
  the `config` in its file. Rows: NEWS2 chart trigger; B4 alone and NEWS2 OR B4; B4 no
  temperature alone and OR; generation 0, the final policy, and the same two without temperature,
  each alone and OR NEWS2. Generation 0 is always the fixed start under the default
  configuration (form `sum`, no frozen vital: weights 1, 1, 1, 1 and 1, 1, 1, 0, theta 3.0, p 4),
  not the final configuration's. It opens only `seed-11` to `seed-30` of the folder given, by
  name, manifest first, and refuses a folder without them. It also refuses unless the ledger has
  its stop line and `policy.json` is the ledger's current incumbent policy (same sha256). Run
  once, at the very end, and the output goes in the card verbatim.
- Limits: validation numbers are optimistic (reused); the data and the episodes come from one
  simulator, so this is circular and synthetic, not validated. A person signs off before anything
  changes: the loop only writes files on a branch.

Run (either venv; `--data` is `ward-v2-ext`, `--out` is `models/tier_b/reward_policy`):

    python -m research.reward_loop --data /data/trials/ward-v2-ext --out models/tier_b/reward_policy --round 1 [--jobs N]
    python -m research.reward_loop --data /data/trials/ward-v2-ext --out models/tier_b/reward_policy --round R --change key=value --why "text" [--jobs N]   # R = 2 to 4
    python -m research.reward_loop --out models/tier_b/reward_policy --stop "reason"
    python -m research.reward_loop --data /data/trials/ward-v2-ext --out models/tier_b/reward_policy --no-temperature [--jobs N]
    python -m research.reward_loop --evaluate /data/trials/ward-v2 --out models/tier_b/reward_policy

A round command resumes by itself when generation files exist, and at the round's stop it judges
the round against the incumbent and appends its ledger line (running it again changes nothing).
Exit codes: 0 done, 2 refused (one line on stderr), 3 seeds are missing (`needs seeds a to b`).

References: Beyer, H.-G. (2000), Computer Methods in Applied Mechanics and Engineering 186,
239-267, https://doi.org/10.1016/S0045-7825(99)00386-2. Jin, Y., Branke, J. (2005), IEEE
Transactions on Evolutionary Computation 9(3), 303-317, https://doi.org/10.1109/TEVC.2005.846356.
Smith, J. E., Winkler, R. L. (2006), Management Science 52(3), 311-322,
https://doi.org/10.1287/mnsc.1050.0451. Dwork, C., Feldman, V., Hardt, M., Pitassi, T., Reingold,
O., Roth, A. (2015), Science 349(6248), 636-638, https://doi.org/10.1126/science.aaa9375.

## The confidence check (slice M3)

Written before any calibration or held-out run. Nothing here is changed after a result is seen.

What is controlled, in plain words: for a patient who is not starting to deteriorate, at most 1 in 100 of
their 15-minute checks shows a supported alert, on average over patients. That bounds false alarms per
patient. It does not bound the share of alerts that are true: positives are about 0.3% of scored bins (0.2735% in training, 0.3143% on validation), so even
when the target is met most supported alerts are still false. One-sided only, against the negative class:
"quiet" means only that the student rule did not fire, and no confident-quiet claim is made (a two-sided
Mondrian version was considered and rejected).

Data and split. Calibration uses ward-v2-ext seeds 301-340 only, all 40: `calibrate` refuses unless exactly those seeds are
given, and `evaluate` refuses a calibration file whose seed list is not exactly them. Seeds 61-300 (student training and
validation) and ward-v2 seeds 11-30 (held out) are never read at calibration: the loader reads `manifest.json`
first and refuses any other seed before it opens any other file, and a seed given twice. Held out is ward-v2
seeds 11-30 through `research.evaluate`, run once. Calibration and held-out must share days, speed and
scenario hash (the file records them; evaluate refuses a mismatch; if so, park M3 and report it).

Method (all constants are in the code: `ALPHA = "0.01"`, `DRAW_SEED = 20261113`).
- Model: M1's ONNX and M1's threshold t, unchanged; their sha256 and t are copied into the calibration file.
  A variant with no exported student or no usable t is "not applicable": no file, no rows, said in the output.
- Score: s = the student's p on a scored (RANKED) bin. Unit: one negative bin (label 0, M1's label rule) per
  non-starting stay that has one, drawn uniformly with SplitMix64 (`bootstrap.py`), index `next() % bins`. The draw
  visits stays by seed in sorted order and, within a trial, in `episodes.csv` order (not sorted by patient). The
  guarantee does not depend on that order: any order fixed before the scores are seen gives one uniform draw per
  stay. One draw per stay gives exchangeable draws whatever the dependence between bins of a stay (Dunn, Wasserman
  and Ramdas, "subsampling once").
- Quantile: with n drawn scores, j = floor(alpha (n + 1)) in exact `Fraction` arithmetic, k = n + 1 - j, q = the
  k-th smallest score; j = 0 gives q = +inf (stored as null), so nothing is supported. A bin is supported iff
  p > q (strict, so ties are conservative). Checked rule: alert iff p >= t and p > q; "not sure" iff p >= t and
  p <= q; "quiet" iff p < t. A "not sure" bin never alerts. The check is binding iff q is absent (null, +inf) or q >= t (the code's rule); if q < t it is not binding, and the output says so.
- alpha = 0.01: below the old student's time in alert (about 2%) so it can bind, and far below NEWS2's (about 16%).

What is reported on held-out, once. Rows `student`, `student_checked`, `news2_or_student`,
`news2_or_student_checked` (`_no_temp` where applicable) next to the NEWS2 rows, with the usual columns. Then a
block: (1) the achieved error, the mean over held-out non-starting stays of each stay's share of negative scored
bins with p > q, with its 95% stay-bootstrap interval (1,000 resamples, seed 20261112) against alpha, and for
context the same for the checked rule, the student alone and pooled over bins; (2) the "not sure" share of scored
bins and of the student's alerting bins; (3) alerting bins and new alerts, student against checked (removing bins can
split an alert, so "removed" can be negative); (4) episodes the student detects and the checked rule does not,
counted both as alerting in any bin of the window and as a new alert before fully in, for all evaluated episodes and
for those not cut short; (5) n, k, q, t, alpha and the file's sha256. The text is also written to `heldout.txt`
beside the calibration; a second held-out run is refused while that file exists (delete it only for a counting
bug, and then show both outputs). It was run once, on 2026-11-13: its outputs are `models/tier_c/confidence_check/heldout.txt` and `heldout.json`, and the line is `heldout.txt sha256 a30cad7664c49dc4faa516ceaca335676bfc86a3bb9e0556ab3296a1d5da8abd`; the command also prints the output text's sha256 for the card. If the
`research.evaluate` sanity guard (a policy with sensitivity 1.0 and no alerts outside the episode windows) ever
refuses the real held-out run, nothing is written and the slice is parked.

MET or NOT MET (fixed now). d = 2 sqrt(alpha (1 - alpha) / n) from the calibration n. MET iff the lower end of the
95% interval in item 1 is <= alpha + d; otherwise NOT MET (the guarantee failed on held-out), which is a finding,
not a defect. "conservative" is also printed when the upper end is < alpha - d.

Commands (the `--model` folder defaults to `models/tier_c/student_vitals`, `--out` to `models/tier_c/confidence_check`):

    python -m research.confidence calibrate <ward-v2-ext folders of seeds 301-340>... [--model <dir>] [--out <dir>] [--cache <dir>] [--workers N] [--run_log <file> --run_id <text> --commit <sha>]
    python -m research.confidence evaluate <ward-v2 folders of seeds 11-30>... [--model <dir>] [--out <dir>] [--json <path>]

`calibrate` writes `calibration.json` (and `calibration_no_temp.json`) with a sibling `.sha256`. `--cache` is the
feature cache of `research.student`. `--run_log` writes one small JSON (alpha, draw seed, seeds, n, j, k, q, t,
binding, the ONNX sha256, package versions, at most 200 quantiles of the drawn scores; all wall-clock numbers under
`time`; no path). `evaluate` refuses unless the file's sha256 (its `.sha256` sibling), ALPHA, the variant, days, speed, the exact
set of fields, j, k and q (which must follow from n), the ONNX sha256 (against the file and
`metadata.json`), t and the scenario hash all match. `evaluate` writes the text and the `--json` file in the same single read. Both commands first run `evaluate_student`'s check of
`metadata.json` against `exports.json` and of each ONNX file against `metadata.json`, so an edited threshold is
refused; an unexpected metadata shape is one refusal line, never a traceback. Exit 2 with one line on any refusal. Tests:
`tests/test_confidence.py` (standard library) and `tests_ml/test_confidence_ml.py`.

Limits. Exchangeability holds only inside the simulator, and stays in one trial are dependent, so this is not a
clinical guarantee ("synthetic, not validated"). The guarantee is marginal: over the calibration draw, the new stay
and the bin within it; it is not per patient or per shift. It is weighted by stay, not by bin: the "pooled over
bins" context figure counts long stays more and can sit above alpha while the guarantee holds. It bounds false
alerts, not precision. Mathematically
the check is a second threshold, max(t, q); its only addition is that q has a stated finite-sample meaning. One
random draw per stay: another DRAW_SEED gives a slightly different q.

References: Vovk, Gammerman, Shafer (2005), Algorithmic Learning in a Random World, Springer,
https://link.springer.com/book/10.1007/b106715. Angelopoulos, Bates (2021), A Gentle Introduction to Conformal
Prediction, https://arxiv.org/abs/2107.07511. Dunn, Wasserman, Ramdas (2022), Distribution-free prediction sets for
two-layer hierarchical models, JASA, https://arxiv.org/abs/1809.07441.

## Plain export of the student (slice M5)

A hand-off so the API can run the student later without numpy, onnxruntime or MindSpore. Nothing is wired in
by this slice.

`python -m research.export_plain [--model <dir>]` (default `models/tier_c/student_vitals`) reads `metadata.json`,
checks each ONNX file against its sha256 and its graph against the exact graph `to_onnx` writes, and writes
`model.json` (and `model_no_temp.json`) next to it. Exit 2 with one line on any refusal. The bytes are
deterministic. The reader and the forward pass are `research/plain_student.py`, standard library only.

The file (format `biosentry-plain-mlp-1`) holds the input names in order, `mean`, `std`, two layers, the
threshold (null when the variant has none), the source ONNX file and sha256, and `synthetic: true`,
`validated: false`. `weights[unit][input]`: the outer index is the output unit, the inner index is the input
(the ONNX initialiser is `[input][unit]`, so it is transposed). Every number is the float32 value written with
Python's `repr`, so it reads back exactly. The caller passes the feature vector exactly as defined above, with
its missing flags; a wrong length or a non-finite value is refused, never filled.

Tolerance: on a fixed batch of 1,000 rows the plain forward pass (double precision, the ONNX graph's order) is
within 1e-6 of onnxruntime and within 1e-5 of MindSpore and MindIR. The test that compares the committed
`model.json` with the committed models runs in the ML gate.
Tests: `tests/test_plain_student.py` and `tests_ml/test_export_plain.py`.

## Manifest, run logs and notebooks (slices M4 and M6)

Packaging and documentation only: no model is trained or changed, no held-out seed is read, nothing is uploaded.
`models/README.md` has the layout and the fixed rule for what counts as a model file; `training/README.md` has the
commands, the run logs and the notebooks.

- `research/manifest.py` (standard library only): `python -m research.manifest --write` makes `models/manifest.json`
  (path, model, framework and version, format, bytes, sha256, training seeds, run id, `commit_at_run`; sorted, deterministic
  bytes) from the files and their own metadata; `--check` exits 2 with one line per difference; `--registry` rewrites
  only the models table of `training/REGISTRY.md`. A field recorded nowhere is `null`. `commit_at_run` comes from the run
  logs (research code starts no process, so not from git). Tests: `tests/test_manifest.py`.
- Run logs are `training/runs/<model>/<run id>.json`. The student's comes from `train_student --run_log`, the
  confidence check's from `confidence calibrate --run_log`, the reward policy's from
  `python -m research.reward_loop --run-log <file> --run-id <id> --commit <sha> --out <policy folder>`, which only reads
  the committed generation records, policy files and ledger (no trial is opened, no wall time). Tests:
  `tests/test_reward_runlog.py`.
- Notebooks under `training/notebooks/` read the newest run log and the committed result files and draw them. They
  need `nbclient`, `ipykernel` and `matplotlib` (exact pins in `pyproject.toml`, research environment only);
  `python training/notebooks/execute.py` runs them and saves their outputs. `tests_ml/test_notebooks.py` runs them
  headless in the ML gate (no extra flag) and fails on an error, an absolute path, a read under a trials folder, or an
  import of MindSpore, scikit-learn, onnxruntime or `research.student`.

## Evaluation export (for the Analytics page)

The page types no number. Each file it reads is made by a command, never by hand: the same inputs and the same
`--date` give the same bytes. This repository owns two exported files (evaluation data contract v3.6): `evaluations.json`
and `registry.json`. Standard library only; `registry` reads the cards and needs no trial data.

```
python -m research.evaluate <ward-v2 folders> --json held-evaluate.json
python -m research.evaluate_student <folders as above> --json held-student.json
python -m research.reward_loop --evaluate <ward-v2> --out models/tier_b/reward_policy --json held-reward.json
python -m research.confidence evaluate <ward-v2 folders 11-30> --json held-confidence.json
python -m research.export_evaluation evaluations --date YYYY-MM-DD --commit <40 hex> --source "<command line that made held-student.json>" --source "<command line that made held-confidence.json>" --held-out held-student.json --held-out held-confidence.json [--drop-rule <id>]... [--confidence held-confidence.json] --out frontend/src/data/evaluation/evaluations.json
python -m research.export_evaluation registry --date YYYY-MM-DD --commit <40 hex> --out frontend/src/data/evaluation/registry.json
```

`--json` holds the same numbers as the printed table, from the same pass (full precision; the exporter rounds). The
printed text does not change. The path is checked first: if it cannot be written, nothing is read or written. The file is written through a scratch file with a name nothing else has (an existing file is never touched), and a write error after the run is a one-line refusal with exit 2. Held-out
seeds are read once per model. A text report is never a source of a number, so a result that exists only as text is
absent from `rules` (the exporter says so in `notes`). A rule in two held-out files must have the same numbers; more
than 30 rules need `--drop-rule`, which writes a line in `notes` naming each dropped row. `--source` is required for `evaluations` (once for each command line that made a held-out file). `--confidence` names the confidence check's held-out JSON (it may be a `--held-out` file too); its `confidence` block is copied with floats rounded to 4 decimals, with its repository path and sha256 (null, with a note, without it). `evaluations.json` has format `biosentry-evaluation/4`. The files refuse a banned word, a number with no plain spelling and a cap that is
broken, and write nothing then.

## Golden fixture for the wiring slice

`research/tests/data/wiring/golden.json` is a committed golden file. The backend cannot import `research/`, so it
tests its own feature builder, forward pass, confidence check and policy state machine against it, bin by bin.
Make it again (ML venv, deterministic bytes): `python -m research.wiring_fixture --out research/tests/data/wiring/golden.json`.
Shape, format `biosentry-wiring-golden/1`:

- top level: `about`, `bin_minutes`, `files` (path, bytes, sha256 of each model, calibration and policy file), `constants` (t, q, policy params and form, baseline counts, lags), `feature_names`, `stays`, `summary`;
- a stay: `id`, `source`, `admit_minute`, `bins`: every 15-minute boundary from the first row to the last (`minute` is the END of the bin; a gap has `row` null);
- a bin: the stored `row`, `news2` (ranked, total, temperature points), `baseline_formed` per vital;
- a scored bin: per variant `student` (float32 `features`, `p`, `check`) and `policy` (clipped `z`, `score`, `alerting`);
- a NOT RANKED bin has `student` and `policy` null and `policy_run_reset` true.

Stays: the toy trial and one synthetic 94-bin stay `synthetic-long`: gaps at bins 30 and 84, NOT RANKED bins (consciousness
missing) at bins 45 and 78, new blood pressure readings at bins 54 and 74 and oxygen switched on at bin 72. Bins 30 and 45
come while the policy runs are 0. Bins 78 and 84 come inside the alerting stretch, so both policies go alerting, reset, not
alerting for p - 1 scored bins, alerting again; `summary.totals.resets_after_run` counts these per variant. The file holds what the research code computes on every scored bin. It does not apply the product's
gates (nothing computed while the baseline forms; the 672-bin cap): `baseline_formed` lets the backend test its own.

## Real data (eICU-CRD Demo 2.0.1)

The first real dataset: an open ICU database (PhysioNet, ODbL, no account). It measures NEWS2 and the
per-patient baseline rule on real records. No model is trained here. Card: `models/data/eicu-crd-demo/card.md`.
Code: `research/realdata/` (standard library only; imports the backend's NEWS2 and baseline).

Get it. Official source only: https://physionet.org/content/eicu-crd-demo/2.0.1/ (about 130 MB). Keep it
outside the repo as `~/Desktop/Projects/biosentry-data/eicu-crd-demo/2.0.1/` with a `SOURCE.md` (URL, licence,
every file's sha256, and whether it matched the official `SHA256SUMS.txt` from physionet.org). The loader
hashes every file listed in `SOURCE.md` on each run and refuses a mismatch, a missing file or a row
marked as not matching. Never commit the data or row-derived files (our privacy rule; any published derivative database would be ODbL share-alike).

Run (inside either gate's venv; stdlib only, about 30 seconds):

    python -m research.realdata.evaluate /data/eicu-crd-demo/2.0.1

How the rows are made (`loader.py`, written before any analysis):
- Test set: the 46 hospitals the generator fit (fit-generator.mjs) did not use; 38 of them were used by the circadian calibration fit (fit-eicu.mjs), so this is mostly, not fully, a different-site test (hospital ids sorted, every 4th, `scripts/fit-generator.mjs:71` in the sim repo; encoded as ids in `split.py`, re-derived from the data on every run). Only those hospitals are loaded. Nothing is tuned on them: the baseline uses `params.json` as it is. Note (ward-v2): the eICU results were produced with k = 3.0, p = 4, the ward-v1 sweep choice. `research/realdata/evaluate.py` reads `models/tier_b/baseline/params.json`, which now holds the ward-v2 choice (k = 4.0, p = 8), so a re-run would use k = 4.0, p = 8 and would not reproduce the published baseline numbers. It has not been re-run.
- Cohort: adult, first unit stay per patient (smallest stay id), unit stay of 12 hours or more, minus a vasopressor
  before hour 6, minus deaths with "Comfort measures only" or "No CPR" charted over 12 hours before death.
- Event: first vasopressor start (norepinephrine, vasopressin, epinephrine, phenylephrine, dopamine) from hour 6,
  or death in the unit, whichever is first. Each bin asks "event within 12 hours?". Bins at or after the event are
  not built, so an event stay ends at its event. A window that would start before admission is shortened to start at
  admission (counted in the output). The 2-hour tail after "fully in" is 0 here, so event and chance windows
  have the same length.
- Rows: 15-minute bins from unit admission. Heart rate, respiratory rate, SpO2 = median of the values in the bin.
  Systolic = latest cuff or arterial value within 60 minutes. Temperature, GCS (15 = A, else "not A") and oxygen
  device (room air = no, a known device = yes, unclear = unknown): latest charted value within 4 hours, placed at
  its entry time. Past the limit it is missing. SpO2 scale 1 for everyone. Bounds are the backend's own (NEWS2's
  input guards and the API's systolic range 20 to 300), except temperature, held to 30 to 45 C (a Fahrenheit value
  left in the Celsius column is dropped, not converted by guesswork). A value outside is dropped and counted
  (printed), never clamped. Checked in the rows: heart rate has 17 bins at exactly 0 and no value above 183;
  respiratory rate has 912 bins at exactly 0 (monitor or ventilator artefact, scored 3 by the backend rules)
  and 20 above 80, up to 122; SpO2 goes down to 11; systolic 24 to 262. These are inside the backend's bounds and
  were not filtered.
- Missing is missing: no imputation, interpolation or default. NEWS2 with the backend scorer abstains (NOT RANKED)
  unless all inputs are present; "full NEWS2 inputs available: N%" is printed next to it. A second row, "partial",
  adds only the components that were measured: "measured-components sum (NOT NEWS2; lower bound)", the chart's
  trigger applied to that sum. Not-alert consciousness (GCS under 15, sedated and ventilated patients) is present in 18,748
  of the 43,052 bins where it fires and alone causes the firing in 10,767: in an ICU close to an outcome-linked input.
- The alert rule, windows, chance control, excess over chance and patient bootstrap are `research.evaluate`'s.
  AUROC and AUPRC are over bins (NEWS2 total against "event within 12 hours"), with the same patient-level
  bootstrap (resampling per-stay score counts). The minimum detectable AUROC difference is Hanley and McNeil's
  at AUROC 0.75, 80% power, with an assumed paired correlation of 0.5 (also printed unpaired).
- The baseline rule's limits: its parameters (k 3.0, p 4 for the published results; ward-v1 sweep) were tuned on simulated data, and its baseline is the
  first 6 ICU hours, often the sickest; for events at 6 to 12 hours the baseline period overlaps the event window.
- Excess over chance is printed with its ceiling (1 minus chance) and its share of the ceiling. Excess cannot be compared between rules with different chance rates; as a share of ceiling the order changes, and the intervals overlap.
- Output hides any count under 5, and any count whose complement is under 5 (printed as ">19/24") ("<5") and prints no patient rows or ids.

What the numbers mean and do not mean:
- They say how NEWS2 and the baseline rule behave on ICU patients from other hospitals than the ones the
  simulator was fitted on. They do not validate either rule for wards: ICU, US, 2014 to 2015, monitor-driven.
- The event is a treatment decision (vasopressor) or death, not a ward transfer.
- The test set is small (tens of events). Intervals are wide and a difference smaller than the minimum
  detectable one cannot be told from noise.
- Full NEWS2 inputs are rare because the oxygen device and GCS are charted sparsely. When NEWS2 abstains on most
  bins, "NEWS2 chart trigger" alerts rarely; read it with its abstention row, never alone.
- Required wording: "tested on eICU-CRD Demo, an open ICU dataset; not validated for ward use". Public use also
  needs the ODbL notice in the card. The output ends with "real ICU data (eICU-CRD Demo), open access; small test
  set; not validated for ward use".

The output ends with: "With 24 events and full NEWS2 inputs in 5.5% of bins, this run cannot rank NEWS2, the measured-components sum or the baseline rule against each other; no conclusion can be drawn." followed by the provenance line.

Tests: `tests/test_realdata.py` on a tiny made-up fixture shaped like the eICU tables (no real row anywhere).

## Limits

- Temperature. On ward-v1 the sim added a fixed rise (`INJECT_TEMPERATURE = 0.6`) in every
  episode kind with a noise of 0.02, so any detector that read temperature looked better than it
  should. Sim PR #25 removed the rise and set the noise to 0.1 C (a marked judgement); ward-v2 is
  made with that fix. On ward-v2 the learned rules and the baseline add nothing measurable to
  NEWS2 (see the model cards).
- Circular. The data and the episodes come from the same simulator that the alerts are judged
  on, so this says how a rule behaves on this simulator, not on patients. A model trained on
  simulator trials and then scored on simulator trials is circular in the same way. Output ends
  with "synthetic, not validated".
- "Higher care" (`outcome`) is decided by NEWS2 inside the sim, so it is never an event.
- Read sensitivity next to its chance rate. With alerts this frequent, a window laid on a patient
  with no episode often contains a new alert or an alerting bin by itself, so only the excess over
  chance can be attributed to the episode.
- Hypotensive episodes are visible only once a nurse takes a cuff pressure. The output prints how
  many hypotensive windows have no cuff reading carried by a bin inside them (NEWS2 saw no new
  blood pressure); the low
  hypotensive sensitivity comes from that and from the new-alert rule (an alert already open at the
  window start does not count), not from one cause alone.
- The toy cohort in `tests/data/toy` has gaps on purpose (a few dozen rows, hand-computed
  answers, `toy_expected.txt` is the exact expected output) and a hand-set `patient_days`.

## Gate (RESEARCH-GATE)

    docker run --rm -v <repo>:/app -v ~/Desktop/Projects/biosentry-data:/data:ro -v biosentry-test-venv:/venv -w /app -e PYTHONPATH=/app/backend/src:/app -e PYTHONDONTWRITEBYTECODE=1 biosentry-api-build sh -c "/venv/bin/python -m pytest -q -p no:cacheprovider research/tests; echo pytest-exit \$?"

It must print "N passed" and `pytest-exit 0`. If `/venv/bin/python` is missing, run
`uv sync --locked -q` in `/app/backend` first with `-e UV_PROJECT_ENVIRONMENT=/venv -e
UV_PYTHON_DOWNLOADS=never`. The backend's coverage gate does not measure `research/`.

## Gate (ML-GATE)

RESEARCH-GATE does not collect `research/tests_ml/` (its venv has no numpy). This gate runs both
folders in the research venv, so the student tests are never skipped:

    docker run --rm -v <repo>:/app -v ~/Desktop/Projects/biosentry-data:/data:ro -v biosentry-research-venv:/venv -w /app -e PYTHONPATH=/app/backend/src:/app -e PYTHONDONTWRITEBYTECODE=1 -e LD_LIBRARY_PATH=/venv/lib biosentry-api-build sh -c "/venv/bin/python -m pytest -q -p no:cacheprovider research/tests research/tests_ml; echo pytest-exit \$?"

It must print "N passed" and `pytest-exit 0`. The venv's locked packages were checked with pip-audit
on 2026-11-12, before MindSpore 2.11.0 was added; the new lock has not been audited. Set up the venv
once with this command, which syncs the lock and then links a `libgomp` that MindSpore needs:

    docker run --rm -v <repo>:/app -v biosentry-research-venv:/venv -w /app/research -e UV_PROJECT_ENVIRONMENT=/venv -e UV_LINK_MODE=copy -e UV_PYTHON_DOWNLOADS=never biosentry-api-build sh -c "uv sync --locked && ln -sf \$(ls /venv/lib/python3.11/site-packages/scikit_learn.libs/libgomp-*.so.1.0.0 | head -1) /venv/lib/libgomp.so.1"

`shortcut`: the image has no system `libgomp`, so the line borrows the copy shipped inside the
scikit-learn wheel. On a normal machine install `libgomp1` instead and drop the link and
`LD_LIBRARY_PATH`.

## The research agent (`research/agent/`)

Written before any real run. Standard library only. It runs the experiments above through a small
fixed set of tools, writes every step to a ledger, and changes nothing under `models/` until a
person has signed off. Everything it reads is simulated.

Run one round of the loop:

    python -m research.agent run [--rounds N]

One round: it reads the finished rounds in the ledger, picks the next experiment, writes a `start`
record, runs the tool, and writes the result and a conclusion. The fixed list of experiments, and
the rule that picks one when no model is configured (first that fits):

- R1 no reward round with temperature yet: run `research.reward_loop` (about 11 minutes).
- R2 that round chose a policy not yet checked: score "NEWS2 chart trigger OR the policy" against
  the chart trigger on tuning seeds 1 to 7, which the loop never reads.
- R3, R4 the same pair without temperature.
- R5 no sweep yet: `research.sweep` on tuning seeds 1 to 10.
- R6 nothing left: it says so and stops.

A proposal is written by code, never by a model, and only when its rule holds. Fixed before any
run and not tuned afterwards:

- Adopt a reward policy: the OR rule raises at most 25% more alerts than the chart trigger (the
  sweep's budget); its early-catch excess over chance is at least 0.05 above the chart trigger's
  (a judgement); and the policy file differs from the installed one.
- Set the baseline's k and p: the sweep chose a setting and it differs from the installed one.

Sign off, then apply (two separate commands, typed by a person):

    python -m research.agent status
    python -m research.agent approve P-0001 --by "<your name>"
    python -m research.agent apply P-0001

`approve` records who and when, and the operating-system user. `apply` refuses unless the ledger
chain is whole, the proposal is approved, and every source file still has the sha256 that was
proposed; then it copies those bytes into the models folder and records the hashes before and
after. It is the only code in the package that writes there. If a copy fails part way, what was
written is recorded as `apply_failed` and `apply` can be run again (the true hashes from before
are then in the `apply_failed` record).

Ask a question:

    python -m research.agent ask "What if we alerted at a NEWS2 total of 7 or more?"

The question is matched by fixed keywords to one of three experiments, all on tuning seeds 1 to
10: a different NEWS2 alert threshold against the chart trigger; a patient's own baseline against
NEWS2; alerts per nurse per 12-hour shift. The only thing taken from the text is one whole number
from 1 to 20. The answer is fixed sentences filled with numbers from the tool result, always with
the chance control, and it ends with "Simulated patients. Not validated. A person decides." Every
number in the answer is checked: with the fixed phrases removed, the numbers in the text must be
exactly the values taken from the tool result and the arguments, in the same order. If not, or if the question fits
no experiment, the answer is one fixed text that says what it can run. An episode counts when the
rule had an alert on in any bin from the episode's start to fully in (no two-hour tail), on
episodes not cut short ("with a complete window"). An alert already on when the episode began
counts, which is why the chance control is always printed next to it. A keyword list refuses the
common forms of a question about one patient, a treatment, real or held-out data, or a request to
change or approve anything; it does not catch every form (a surname alone gets through). Also
refused: "above 5" or "under 7" (which rule?), a decimal, a sign, or a second number. `eval_questions.json` holds
the agent's own test questions (44, 23 of them refusals) and runs in the gate. Two numbers are read only as a
comparison that says which is the new threshold and names the current trigger, 5, as the old one
("7 instead of 5", "from 5 to 7", "to 7 from 5"), next to a threshold word; "7 instead of 6",
"between 5 and 7" and "5 or 7" are refused. An order to change something ("raise the threshold to
7", "set it to 7 going forward") is refused; the same change asked as a what-if ("what happens if
we raise the threshold to 7?") runs the experiment.

Settings (environment variables; all optional):

- `BIOSENTRY_AGENT_TRIALS`: the trial root. Default `~/Desktop/Projects/biosentry-data/trials`.
- `BIOSENTRY_AGENT_HOME`: the ledger (`ledger.jsonl`) and run outputs (`runs/S-NNNN/`). Default
  `~/Desktop/Projects/biosentry-data/agent`. Never inside the repository or the models folder.
- `BIOSENTRY_AGENT_MODELS`: the folder `apply` writes to. Default this repository's `models/`.
- `BIOSENTRY_AGENT_SIM`: a simulator checkout, for `compare_runs`.

The model API is off unless all three of these are set:

- `BIOSENTRY_AGENT_MODEL_URL`: the full chat-completions address of an OpenAI-compatible service.
  It must start with `https://`.
- `BIOSENTRY_AGENT_MODEL_NAME`: the model's name on that service.
- `BIOSENTRY_AGENT_MODEL_KEY`: the key, at least 16 characters. It is sent only in the
  `Authorization` header, and is never printed or written to the ledger.

The intended target is an open-weight model on Huawei Cloud's model service (ModelArts Studio,
MaaS). Checked in its documentation on 2026-11-13: the international site serves its
OpenAI-compatible API from CN-Hong Kong only and lists `deepseek-v4-flash` (DeepSeek-V4-Flash,
weights published under the MIT licence). Example values, not defaults; nothing is set in code:

    BIOSENTRY_AGENT_MODEL_URL=https://api-ap-southeast-1.modelarts-maas.com/openai/v1/chat/completions
    BIOSENTRY_AGENT_MODEL_NAME=deepseek-v4-flash
    BIOSENTRY_AGENT_MODEL_KEY=<a MaaS API key, from the environment only>

Both stay settings because a second documentation page shows the path `/v2/chat/completions`, and
the service retires models within months. Sources:
https://support.huaweicloud.com/intl/en-us/model-call-maas/model-call-021.html and
https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash .

With it on, the model is shown the tool list and a digest of the ledger (ids, experiment names
and numbers only) and must answer with one tool call as JSON. The call is used only if it parses,
passes the tool's schema, and equals one of the experiments that are open at that point. Anything
else (an error, a timeout, other text, another call) is logged as `planner_fallback` and the rule
above decides. So the model can change the order of the open experiments and nothing else. It
cannot propose, approve or apply. The doctor's question never goes to the model.

Limits:

- Nothing here has called a real model service. The model planner is tested only against recorded
  answers. No Huawei Cloud account was used.
- `compare_runs` always answers "not available": the simulator's paired `npm run compare` did not
  exist when this was written, and the tool does not run a command line it has not been checked
  against.
- The proposal rules use point estimates on a few dozen episodes, with no interval. A proposal is
  a request for a person to look, not a finding.
- Question matching is keyword based. A question in other words gets the fixed refusal, and the
  refusal list is wide (any "her", "room", "oxygen", "email" is refused) yet not complete. What
  makes this safe is not the list: a matched question only ever runs one of the three experiments
  on simulated tuning trials, and any other words in it are ignored, never carried out.
- A check that fails is tried once more in a later round, then left, so it cannot block the rest
  of the list.
- The grounding check reads digits. A number written in words would not be caught; the fixed
  sentences contain none.
- The model call's 20-second timeout is per network operation, not for the whole call.
- The ledger's hash chain shows an edited or removed line, but not an edit to the last line only,
  and not a change by someone who rewrites the whole file. It is a record, not access control:
  anyone who can write the files can also write `models/` directly.
- The in-process time cap is a signal on the main thread of a Unix process. The reward loop runs
  as a child process with a timeout and a 16 MiB cap on any file it writes.
- The agent never scores held-out seeds 11 to 30. The schema refuses them, and the folder check
  refuses a manifest that names one.
- Circular, as everything above: rules are judged on the simulator that made the data.

Export for the evaluation site (read only; it writes one JSON file and nothing else):

    python -m research.agent export --date YYYY-MM-DD --out <file.json> [--ledger <ledger.jsonl>] \
        [--commit <40 hex>] [--source "<command line>"]... [--scores <scores.json>]

The shape is `biosentry-evaluation/1`, section `research-agent`: `provenance`, `body`, `notes`. The date
comes from `--date`, never the clock. Without `--commit` it asks git. `--ledger` reads another ledger
file with the same checks. `body.questions` (last 200): `id`, `asked_at`, `question`, `template`,
`status`, `answer` (lines), `numbers`. Each answer is rebuilt from the stored tool result and must
match the ledger, or the export is refused. `body.proposals` (last 50): `id`, `action`, `step`,
`conclusion`, `state`, `approved_by`, `approved_at`. `body.scores` is null, or the file made by
`runlog.py --scores-out`. A banned phrase or a float refuses the export. Same ledger, same bytes.
The question text is what a person typed: a page must show it as text.

### The question worker (`python -m research.agent worker`)

It answers the questions that clinicians type on the dashboard's Ask page. It claims one question from the
API, runs the same `ask` code as `python -m research.agent ask`, and posts the answer and the record of the
experiment back. The question is data: it is never read as a command. The contracts are `contract-ask` v1.1
and `contract-d7` v3.1.

    python -m research.agent worker [--once]

`--once` makes one claim, handles at most one job and exits 0. There is no other option.

A job id the worker has already handled (it keeps the last 1000) is never run or reported again: it logs
`job <id>: ignored, already handled` and waits 30 s. Each request has a 60 s total deadline, follows no
redirect and uses no proxy from the environment, so the key goes to the API host only.

Three environment variables, read once at start:

- `BIOSENTRY_API_URL`: an `http://` or `https://` address with a host, no user-info, query or fragment.
- `BIOSENTRY_INGEST_KEY`: the ingest key. The worker never logs it and never puts it in a URL.
- `BIOSENTRY_AGENT_COMMIT`: optional, 7 to 40 lower-case hex characters. Without it the record says `null`.

The worker refuses to start (exit 2) without the first two, or when any `BIOSENTRY_AGENT_MODEL_*` variable
is set. The agent's own settings (`BIOSENTRY_AGENT_TRIALS`, `BIOSENTRY_AGENT_HOME`) work as for the other
commands.

It needs Python 3.11, the backend package, the trial root read-only (`ward-v2`, seeds 1 to 10) and a
writable agent home outside the repository. Nothing in this repository starts it, and where it runs is not
decided.

Do not start the worker until the API serves `?kind=question` on the claim (dashboard round 6c, migration
0011). An older API ignores `?kind=` and hands out a clinician's compare run. The worker refuses that job
with the `kind` reason, as the contract says, and the API stores that run as refused. So after the first
job of another kind the worker prints one `refused: ...` line and exits 2. It never claims again, and an
old API loses at most one compare run.

It runs in-process, on the main thread, one job at a time. There is no child-process mode, so the reason
`the agent did not finish within 15 minutes` is never sent. Every report uses one fixed reason:

- `Refused: no template fits this question.`
- `Refused: a number in the answer is not in the tool result.`
- `the worker does not accept this job: kind` or `: question`
- `the agent's tool failed`
- `the agent's ledger could not be written`
- `the agent's answer is over the size cap`
- `the agent stopped with an error`

No exception text, question or answer goes into a log line. A log line is `job <id>: <state>`.

#### Model mode

Off by default. Two more environment values turn it on, and both must be set (one alone: exit 2):
`BIOSENTRY_AGENT_TUNED_BASE` (the base weights folder) and `BIOSENTRY_AGENT_TUNED_ADAPTER` (the adapter
folder). The model needs the optional MindSpore environment of `research/agent_tuning/`. When that is another
Python than the worker's, `BIOSENTRY_AGENT_TUNED_PYTHON` names its interpreter; unset, the worker's own Python
runs the model. The model's process imports nothing of the agent or the backend.

The keyword rules answer first, as before. The worker asks the model only where the rules are silent, so a
question the rules answer never waits for the model (an owner decision, 2026-11-13). The model can only pick
one of the three fixed experiments, or nothing. The guard in `tasks.guard_question` decides: the answer must
be exactly a template and the number written in the question. Where the guard refuses, the answer is the
fixed refusal, as before. On the blind held-out questions 7 of 78 reach the model. No answer can change a setting. The guard cannot check that the model
chose the right experiment. Where the keyword matcher is silent, a wrong pick among the three fixed
experiments can pass, and the doctor gets an answer about another experiment. The record says
`agent.ask: "tuned-model"` and the adapter's sha256.

With the mode on, the question text reaches a language model. Prompt injection can at most make the model
pick another of the three experiments, or refuse. A question that the guard refuses by its own rules never
reaches the model. The model runs in a child process, one question at a time. Its environment holds no
`BIOSENTRY_*` value, so it never holds the ingest key; the two folder paths are its arguments.

Loading has 300 s. One answer has 240 s, and that covers the write to the child too, so a child that does
not read cannot block the worker. A failure kills the child's process group, the rules answer that question,
and a new child starts for the next. A process that left that group is not reached. A failure is: a timeout, a line over 8192 bytes, an
answer over 2000 characters, an answer that is not one of the two allowed shapes, or a child that ends.
After 3 failures in a row the mode is off until the worker restarts. Not a failure: a slow answer in an
allowed shape. A model that always takes just under 240 s makes every question wait that long.

At start, and again before each start of the model process, the router checks the two folders against
`research/agent_tuning/weights.json`. Every listed file must be there with the right sha256. A folder may
hold nothing else but `LICENSE`, `README.md` and `SHA256SUMS.txt`. A file or a weights folder that is a
link is refused; a link higher in the path is not looked at. On any mismatch the mode turns itself off, the
log names the listed file, and the rules answer every question. Not caught: a file that is swapped after the
check and before the model loads it. Mount the folders read-only. The start line says
`model mode: on` or `model mode: off`.

## The patient agent (`python -m research.patient_agent worker`)

The patient agent answers a clinician's question about ONE patient, from that patient's own data only. The card
is `research/patient_agent/card.md`. It has four kinds of question: `own_baseline`, `recent_change`, `why_rank` and
`active_alerts`. The job's `patient` object follows contract-patient-ask (format 1).

A keyword matcher picks the kind. No model chooses anything. `guard` in `patient_agent/ask.py` is for a later
tuned model and is not wired. The answer is fixed sentences filled from the data. Text from the data is quoted, cut at
a space at or before 300 characters, and never read as an instruction. A text that holds a character outside a small
allowed set is not shown at all, because cleaning it could change a number. Every number in the answer is checked against the data.
The agent never gives treatment advice, never predicts and uses no outside knowledge. A question is
refused as about another patient, a bed or the ward when the question says so with a listed word; otherwise the
answer is about this patient only and says so in its first line, "For this patient only:". A question it cannot answer gets a fixed
refusal. A required field that is missing makes the job `failed`.

The worker reuses the question worker's `Worker` class: the same transport (no redirect, no proxy), retries,
handled-id set and key rules. It claims `POST {base}/sim/lab/claim?kind=patient_question` and accepts a claim
response of up to 524288 bytes. It reads `BIOSENTRY_API_URL`, `BIOSENTRY_INGEST_KEY` and, optionally,
`BIOSENTRY_AGENT_COMMIT`. It needs no trials folder and no agent home.

Nothing in this repository starts this worker. The API routes it calls are on `main` since 2026-11-14; the worker
has not run against them. It stops after a job of another kind.

The data lives in memory for one job. The worker writes no file. A log line holds a job id and a state only. The
`done` record holds the kind, the arguments, the numbers and `data_sha256`, the hash of the patient object as
received (the contract's canonical JSON, NaN refused). It holds no copy of the data. A refusal and a failure carry one fixed reason, and never an exception text.

One claim body from the dashboard session's API (made on its branch, before the merge; the seeded patient p-001) is a test
file: `tests/data/patient_question_job.json`. A test checks that the worker's hash of it is the hash that API
stored when the question was asked, and that the job ends `done`. That is one sample, read from a file. The
worker has still never talked to a running API.
The sample was made before the API began to serve a baseline's times as the start of the 15 minutes, and before
`band_low` and `band_high`. It stays as a check of the hash rule only; its baseline times are the old ones.
