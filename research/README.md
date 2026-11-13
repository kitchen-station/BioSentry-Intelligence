# research: offline evaluation of alerting rules (synthetic data only)

Standard-library Python plus an import of the backend's own `biosentry` package. No dependency, no
`pyproject.toml`, nothing here is served by the API. This slice holds the yardstick: how NEWS2
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
1 to 30 is refused.

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

## The distilled vitals student (slice B5)

Written before any training run. Code: `student.py` (features, labels, models, ONNX),
`train_student.py` and `evaluate_student.py` (the commands). These three need numpy,
scikit-learn, onnx and onnxruntime (`research/pyproject.toml`, `uv.lock`); nothing else in
`research/` imports them, so the stdlib parts and RESEARCH-GATE run without them.
Vulnerability check of the locked requirements (pip-audit, 2026-11-12): "No known
vulnerabilities found".

- Bins: one feature row per cohort row (a 15-minute bin), built from that bin and earlier bins
  of the same stay only. A bin with no cohort row has no feature row. NOT RANKED bins are not
  scored (abstention is an output): they get no probability, are not alerting, and are not
  trained on. Their vitals still feed the baseline, as in the B4 policies.
- Features (19), in this order, per vital (heart rate, respiratory rate, SpO2, temperature):
  the value; a missing flag; z from the patient's own baseline (`baseline_finding` with the
  detector's DEFAULTS window and floors, called on the bins up to this one, so it is the same
  baseline as B4); a z-missing flag (set while the baseline is forming, fewer than 24 valid
  bins, or when this bin's value is missing). Then the NEWS2 total, on oxygen (0/1) and
  consciousness not A (0/1). Missing is missing: a missing input is the number 0 with its flag
  set; no clinical value is ever filled in. On the ward-v1 trials, and on ward-v2 (checked from
  the files: 992,988 RANKED rows, none missing a device vital), a RANKED row always has all
  four device vitals, so the value-missing flags are always 0 on scored bins; they are kept so a
  future cohort with gaps cannot be silently imputed.
- No-temperature features (15): the same without the four temperature features, and the NEWS2
  total minus its temperature points (from the backend's own parameter scores). This removes
  every temperature input.
- Label per scored bin, from the generator's true episode only (`outcome`/higher care is never
  a label): positive when `episode_start <= minute <= episode_start + ramp_minutes` (start to
  fully in); bins after fully in until the stay ends are excluded (neither class); every other
  bin is negative, including every bin of a patient with no episode. Starting patients (admit
  minute 0) are left out of training and selection, as they are out of sensitivity: their true
  arrival is unknown and one has a scripted worsening that is not an episode.
- Split: teacher and students train on seeds 1 to 7; early stopping, the choice of student and
  the threshold use seeds 8 to 10; seeds 11 to 30 are scored once by `evaluate_student`. The
  training loader reads a folder's `manifest.json` first and skips a held-out seed before any
  other file of it is opened.
- Teacher: scikit-learn `HistGradientBoostingClassifier`, `random_state=0`, early stopping on
  the loss of seeds 8 to 10 (`X_val`), at most 500 iterations, 20 without improvement.
- Students, trained on the teacher's probabilities for seeds 1 to 7 (soft labels; no true label
  is used to fit them): (a) logistic: a linear model of the teacher's log-odds (probabilities
  clipped to 1e-4 and 1 - 1e-4) fitted by least squares with ridge alpha 1, then a sigmoid; (b)
  MLP: one hidden layer of 16 ReLU units fitted to the same log-odds (scikit-learn
  `MLPRegressor`, adam, alpha 1e-4, `random_state=0`). Inputs are standardised with the mean
  and standard deviation of seeds 1 to 7 (a standard deviation of 0 becomes 1); those constants
  are inside the ONNX graph. The student with the higher average precision against the true
  labels of seeds 8 to 10 is kept; both are recorded.
- Export: the student only, ONNX opset 13, IR version 7, float32, input `x` of shape
  [1, n_features], output `p` of shape [1, 1]; ops Sub, Div, Gemm, Relu, Sigmoid; built with
  `onnx.helper`. Parity: onnxruntime equals the numpy forward pass within 1e-5 on 1,000 rows
  of seeds 1 to 7.
- Threshold: on seeds 8 to 10, the alert rule above is run with "alerting when p >= t" for t in
  0.001 to 0.009 and 0.01 to 0.99 (steps of 0.001 then 0.01). The smallest t whose alerts do not
  exceed the NEWS2 chart trigger's alerts on the same seeds is chosen (matched burden, the
  same patient-days). If none qualifies, nothing is chosen. The teacher gets its own threshold
  by the same rule. "NEWS2 OR student" uses the student's threshold and so carries more alerts
  than NEWS2 alone; its burden is printed.
- Rule for any future selection (written after B5's held-out run; not applied to B5): a
  threshold also qualifies only when its time in alert (alerting bins over bins) on the
  validation seeds is at most the NEWS2 chart trigger's on the same seeds. Counting alerts alone
  is not enough: one alert that stays open looks like a small burden. B5's no-temperature
  models fail this rule (52.7% against 12.4% on seeds 8 to 10), so `evaluate_student` labels
  their rows "no usable threshold: alert stuck open; not a working rule".
- The same recipe is run twice: with all 19 features and with the 15 no-temperature features.
- Held-out rows (`evaluate_student`): teacher, student, NEWS2 OR student, and the three
  no-temperature versions, next to the NEWS2 and baseline rows, with the alert rule, windows,
  chance control and bootstrap above. It retrains on seeds 1 to 10 (deterministic) and refuses
  to run unless the retrained students' ONNX bytes have the sha256 in the model's metadata; the
  student rows then run the committed ONNX file through onnxruntime.

Run (inside `biosentry-research-venv`, see the gate below):

    python -m research.train_student /data/trials/ward-v2/seed-* [--out <dir>]
    python -m research.evaluate_student /data/trials/ward-v2/seed-*

`--out` defaults to `models/tier_c/student_vitals/`.

## The reward-driven alerting policy (slice B6)

Written before any run. Code: `reward_policy.py` (the policy and the reward) and `reward_loop.py`
(the loop and the held-out command). Standard library only; the tests are in `tests/`.

- Policy: six numbers, four non-negative weights (heart rate, respiratory rate, SpO2,
  temperature), a threshold theta and a persistence p (integer 1 to 8). Per bin, for each vital,
  z from the patient's own baseline (`baseline_finding` with the detector's DEFAULTS window and
  floors, called on the bins up to this one, as in B4 and B5), clipped at 0 in the bad direction.
  Score = the weighted sum. Alerting when the score is at least theta in p consecutive scored
  bins. A NOT RANKED bin is not scored: it is not alerting and ends a run (abstention is an
  output). A bin with no row ends a run. A vital whose value is missing, or whose baseline is
  still forming, contributes nothing; nothing is filled in. Causal: a bin reads itself and
  earlier bins only.
- Generation 0 is the B4 rule's equivalent: weights 1, 1, 1, 1, theta 3.0, p 4. B4 alerts when
  ONE vital has z at least 3 for 4 bins; the sum form cannot express "any one", so with equal
  weights and theta = k it alerts whenever B4 does on RANKED bins, and also when several vitals
  are moderately off together (a superset). Unlike B4 it never alerts on a NOT RANKED bin. The
  weights and theta are only defined up to a common factor; the search does not use that.
- Reward for one trial: C - lambda * S * a. C = evaluated episodes (the windows above) with the
  policy alerting in a bin from `episode_start` to fully in (caught early). a = the policy's
  alerts per patient per 12 h on the trial (alert rule above). S = E / a_news2: E evaluated
  episodes of the trial over the NEWS2 chart trigger's alerts per patient per 12 h on the same
  trial, so the penalty is lambda * E * (a / a_news2) and both terms are in episodes. lambda =
  0.5: a policy with NEWS2's alert count must catch more than half the episodes early to score
  above a silent one (judgement, fixed now, never tuned). Hard constraint: time in alert
  (alerting bins over cohort rows) at most the NEWS2 chart trigger's on the same trial; a
  candidate that breaks it on any trial of the set is rejected. `outcome`/higher care is never
  part of the reward. A set of trials is scored by the mean reward.
- Loop: generation g (1 to 10) learns on three training seeds no other generation sees: 31-33,
  34-36, ..., 58-60 of `ward-v2-train` (`ward-v1-train` for the earlier run). It samples 30 candidates around the current parameters
  (weights and theta: plus a normal draw times sigma, weights floored at 0, theta at 0.1; p:
  plus a normal draw times sigma_p, rounded and clipped to 1-8; values rounded to 4 decimals),
  from a SplitMix64 generator (seed 20261112 + g, Box-Muller normals). sigma starts at 0.5 for
  weights and theta and 1.0 for p, and is multiplied by 0.9 after each generation. The current
  policy and every candidate are scored on the generation's three seeds; the policy moves to
  the best non-rejected candidate only if its mean reward is strictly higher than the current
  policy's on those seeds (ties: the first drawn). A (1+30) evolution step: black-box search
  with a reward, not deep or per-step reinforcement learning.
- Validation and stop: after each generation (and at generation 0) the current policy is scored
  on ward-v2 seeds 8 to 10. The shipped policy is the generation with the highest validation
  reward among those not rejected there (ties: the earlier generation). Seeds 1 to 7 are not used.
- Files: `models/tier_b/reward_policy/generations/gen-NN.json` and `policy.json` (with its own
  sha256); the no-temperature run writes the same under `no_temperature/`. Same inputs give the
  same bytes.
- No-temperature run: the same loop with the temperature weight fixed at exactly 0 (never
  sampled), generation 0 weights 1, 1, 1, 0.
- The loaders open `<root>/seed-NN` only for the seeds named above, read `manifest.json` first,
  and refuse a seed that differs from the folder name or is one of 11 to 30 before any other
  file is opened. Held-out seeds 11 to 30 are scored once, by `--evaluate`, which refuses a
  `policy.json` whose sha256 does not match its content. Rows: generation 0 and the shipped
  policy, each alone and OR NEWS2 chart trigger, for both runs, next to NEWS2 and the B4 rows,
  through `research.evaluate` (same windows, chance control, bootstrap).

Run (either venv):

    python -m research.reward_loop --train /data/trials/ward-v2-train --validate /data/trials/ward-v2 --out models/tier_b/reward_policy [--no-temperature]
    python -m research.reward_loop --evaluate /data/trials/ward-v2 --out models/tier_b/reward_policy

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

## Gate (ML-GATE, slice B5)

RESEARCH-GATE does not collect `research/tests_ml/` (its venv has no numpy). This gate runs both
folders in the research venv, so the student tests are never skipped:

    docker run --rm -v <repo>:/app -v ~/Desktop/Projects/biosentry-data:/data:ro -v biosentry-research-venv:/venv -w /app -e PYTHONPATH=/app/backend/src:/app -e PYTHONDONTWRITEBYTECODE=1 biosentry-api-build sh -c "/venv/bin/python -m pytest -q -p no:cacheprovider research/tests research/tests_ml; echo pytest-exit \$?"

It must print "N passed" and `pytest-exit 0`. The venv's locked packages were checked with
pip-audit on 2026-11-12: "No known vulnerabilities found". Set up the venv once with
`docker run --rm -v <repo>:/app -v biosentry-research-venv:/venv -w /app/research -e UV_PROJECT_ENVIRONMENT=/venv -e UV_LINK_MODE=copy -e UV_PYTHON_DOWNLOADS=never biosentry-api-build uv sync --locked`.

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
the agent's own test questions (34, 18 of them refusals) and runs in the gate.

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
