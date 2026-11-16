# The research models in the API: what runs

This file says what the server does with the research models today. It started as a hand-off written before the wiring.
The wiring has landed, and this file now follows the code. Where this file and the code disagree, the code is right.
Every claim here was checked against the code on 2026-11-17. The checks are listed in `docs-evidence.md` at the
repository root (not committed).

Section numbers and ruling numbers are kept, because code comments cite them.

## Status (2026-11-17)

What runs on the server, read from the code. `backend/src/biosentry/api/app.py` loads the files once at start with
`services/models.py`. `services/model_signals.py` computes the signals. `services/ward.py` passes them to the arbiter in
`domain/arbiter.py`.

- **Prediction model flag.** The student with the confidence check. It adds the reason "Prediction model flag (synthetic,
  not validated)" only when the check says "alert". "Not sure" shows no flag.
- **Reward policy: dropped.** Owner decision models-rl-5 dropped it, after held-out set 2 showed a loss for NEWS2 plus
  the policy. The server does not load `policy.json`, does not compute the policy, and sends no `alert_policy` field
  and no `alert_policy_flag` rule. The policy stays a research result (its card, the Research page). The prediction
  model flag is the only model flag.
- **Own-baseline finding.** It runs as a reason and a tie-break, on its code defaults (k 2.0, p 3), not on the research
  `params.json` (k 4.0, p 8). It covers heart rate, respiratory rate and SpO2. Systolic blood pressure is a fifth vital of
  the baseline route. It is shown, and it feeds no rank and no reason (section 3).
- **Only ranked units are scored.** The server scores NEWS2, the baseline and the models for a unit of kind `ranked`
  only. A `shown` unit (the ER) gets a NEWS2 band and no rank today; ranking the ER as an aid is decided (orch-h10) and in progress. An `unranked` unit (intensive care) gets neither.
- A flag never moves a patient and never lowers a NEWS2 trigger. NEWS2 is unchanged.
- Research results: held-out set 2 (400 trials, 1,743 episodes) is the result to quote. Set 1 (91 episodes) is the small
  first set. Added to NEWS2, the student is a loss (-0.040), the student with the check a small gain (+0.018, 1.048 times
  the alerts), the reward policy a loss (-0.0127, at the edge). See the three cards and `models/held_out_2/analysis.txt`.

## 1. Scope and status

Three offline research models were built. Two run in the API. One was dropped.

| Model | Folder | What it gives per scored bin | Where it is in the code |
|---|---|---|---|
| Distilled vitals student | `models/tier_c/student_vitals/` | a probability p | `TierCSignal.probability` |
| Confidence check on the student | `models/tier_c/confidence_check/` | `alert`, `not_sure` or `quiet`, from p | `TierCSignal.check` |
| Reward-driven alerting policy | `models/tier_b/reward_policy/` | nothing: dropped (models-rl-5) | not connected (section 5) |

- All three are research only. They were trained and judged on synthetic simulator trials. They are not
  validated. No claim about real patients is made.
- Held-out results are in the cards: `models/tier_c/student_vitals/card.md`, `models/tier_b/reward_policy/card.md`,
  `models/tier_c/confidence_check/card.md`.
- Nothing here changes NEWS2. NEWS2 is never learned, re-weighted or overridden. NEWS2 stays the only thing that sets
  `escalated` and the only model-free input to the order.
- Fixed rules (owner decision M-2): a model may flag and never outranks or lowers a NEWS2 trigger. `not_sure` shows
  as no flag. A missing or bad model file means not computed, never a default.
- Each model ships in two variants, with and without temperature. Only the with-temperature variant (`all`) is wired.
  The server never opens the `no_temp` files.

## 2. Where it runs: inside the API (owner decision H-1)

The student and the confidence check run inside the API process on the team server. There is no model service and no
network call per score. No Huawei Cloud account is needed. The API does not import onnxruntime, MindSpore or numpy.

### 2.1 What the API image holds and loads, once, at start

The image copies the whole `models/` folder to `/app/models`. The API reads from it once, at start. The folder
can be moved with `BIOSENTRY_MODELS_DIR`. Without the files the API still starts and ranks by NEWS2, and each model
shows as "not computed".

Four files are read:

| File | Role |
|---|---|
| `models/manifest.json` | the checksums |
| `models/tier_c/student_vitals/model.json` | the student, variant `all`, format `biosentry-plain-mlp-1` |
| `models/tier_c/confidence_check/calibration.json` | q and what it binds |
| `models/tier_c/confidence_check/calibration.json.sha256` | the calibration file's own checksum |

The ONNX, MindIR and `.ms` files are never loaded. The ONNX sha256 is needed only as a value: it is inside `model.json`
(`source.onnx_sha256`) and inside the calibration file, and the two are compared. The student has 35 inputs, a hidden
layer of 32 units and the threshold t = 0.006.

The API never picks a variant by its held-out row and never switches files per row.

### 2.2 The checksum rule

At start, for each file:

1. Read the bytes once, with a size cap: `model.json` 1 MiB, calibration 64 KiB, manifest 256 KiB, the `.sha256`
   sibling 128 bytes. A file must be a plain file inside the models folder (no link on the way).
2. Find the one record with the same `path` in `manifest.json`. Its `bytes` and `sha256` must match the file. No record,
   two records, or a record with no run id means refused.
3. Then the file's own checks:
   - Student: the format is `biosentry-plain-mlp-1`; the name is `tier_c/student_vitals`; the variant is `all`; it has a
     threshold; the reader's checks pass (section 4.6).
   - Calibration: its sha256 equals the text of its `.sha256` sibling, and it parses with exactly the 14 fields.
     `alpha` is `"0.01"`, `draw_seed` is 20261113, and `(j, k)` follow from `n`. It binds this student: same variant,
     same `onnx_sha256`, same `t` (section 6.3).

The manifest is the source of the byte counts and checksums. `python -m research.manifest --write` makes it again when a
file changes. The manifest has no checksum of itself. A person who changes a file and the manifest together is not
detected. The review of the pull request is the guard.

### 2.3 Failure behaviour

A refused file gives `NotComputed(reason)` with a fixed reason, and one warning line at start. The warning holds the
path inside the models folder and the fixed reason, never the file's contents. There is never a default probability,
threshold or q. The API keeps serving NEWS2 unchanged.

| Case | `probability` | `check` |
|---|---|---|
| No usable manifest, or the student file refused (missing, unreadable, over its cap, wrong sha256, no record, wrong format, not the wired student) | `NotComputed("student not loaded")` | `NotComputed("student not loaded")` |
| Calibration or its `.sha256` refused, or it does not bind this student | `Computed(p)` | `NotComputed("calibration not loaded")`: no flag |
| A NOT RANKED patient | `NotComputed("not ranked")` | same |
| A vital not counted: the patch is not sampling within 30 minutes, or the live value is not from the patch | `NotComputed("a vital is not counted")` | same |
| The last stored bin does not score when rebuilt from stored inputs | `NotComputed("latest bin not scored")` | same |
| Baseline still forming for any of the four vitals | `NotComputed("baseline forming")` | same |
| Stay past 672 bins (7 days), or no stored bins, or the vitals not on one grid | `NotComputed("stay past 7 days: admission baseline not kept")`, `"no stored bins"` or `"vital series not on one grid"` | same |
| An impossible value in the baseline window or in a lag bin | `NotComputed("impossible value in a lag or baseline bin")` | same |
| A feature out of range (beyond float32) | `NotComputed("feature out of range")` | same |

- A load failure is found once, at start. The result is fixed for the life of the process. A new file needs a new
  image and a restart.
- A refused manifest disables both models. A refused student disables both (q binds one student). A refused calibration
  disables the check only.
- A failure at score time is logged once per reason per process (section 8.5).
- If the model code raises, the ward read logs `model error` once and serves the NEWS2 list with every model not
  computed.

### 2.4 The forward pass in the API

`backend/src/biosentry/domain/scoring/tier_c/plain_mlp.py` is a verbatim copy of `research/plain_student.py`, under a
two-line header that names the commit it was copied at. It needs only the standard library. A test fails when the copy and
the research file differ (`test_plain_mlp.py`). To change it, change the research file and copy again.
`research/` imports the backend, and nothing under `backend/` imports `research/`. The feature code is a port
in plain Python (`tier_c/features.py`), built on the backend's own `baseline_finding`.

### 2.5 Time per score

The repository holds one timing, for the student's ONNX file under onnxruntime (`models/tier_c/student_vitals/timing.json`):
a median of 8.58 microseconds and a 99th percentile of 26.63 microseconds per call, on aarch64, with one thread, over
10,000 calls after 1,000 warm-up calls. The API does not run the ONNX file. No timing of the pure-Python forward pass,
the feature computation or the check is kept here, and nothing was timed on the team server.

### 2.6 What "no Huawei Cloud account needed" means here

Scoring is local arithmetic in the API process: the student's two small layers and the check's comparison. Training
(MindSpore) and the MindIR and MindSpore Lite files belong to the research side. The in-process path does not use them.
Nothing is sent to a cloud service.

## 3. The bin, the stay and the history the features need

- Bin: one 15-minute bin per patient (`BIN = 15 minutes` in `services/scoring.py`). A bin with no row, or a null or
  impossible value, is a gap. Nothing is filled.
- Bin time: a bin stored by a simulator run is stored at the END of its 15-minute window. `model_signals.bin_window` is
  the one place that says so. (The seed's comments say START; for a posted run they are wrong.)
- Current bin: the last stored bin. The arbiter scores NEWS2 on the live vitals, so a flag can lag NEWS2 by up to 15
  minutes.
- Scored bin: a bin whose stored inputs give a NEWS2 score. On such a bin all four device vitals are present.
- Stay: one patient's stay in one unit. All history is of that stay only.

| Need | Value | Where |
|---|---|---|
| Own baseline window | the first 24 valid bins of the stay | `DEFAULTS.window_bins = 24`, `tier_b/baseline.py` |
| "Forming" | no z until the stay has 24 valid values of that vital | `baseline_finding` returns `NotComputed("baseline forming, n of 24 bins")` |
| 1-hour lag | 4 bins earlier | `features.LAG_1H` |
| 4-hour lag | 16 bins earlier | `features.LAG_4H` |
| Cap on the stay | 672 bins (7 days); past it, not computed | `scoring.MAX_BINS` |
| Patch counted | the patch sampled within 30 minutes and the live value is from the patch | `scoring.STALE_AFTER`, `scoring.counted` |

- The baseline detector runs on its code defaults: k 2.0, p 3, and floors of 2.0 (heart rate), 1.0 (respiratory rate),
  1.0 (SpO2), 0.1 (temperature) and 5.0 mmHg for a charted vital.
- The own-baseline finding that the ward uses as a reason and a tie-break covers heart rate, respiratory rate and SpO2
  (`HISTORY_VITALS`, read by `scoring.drift`). The patient read keeps no temperature history, so the baseline route says
  temperature is unavailable (`no_history`). The student still gets a temperature z, from the stored temperature bins that
  `model_signals` reads with its own query.
- Systolic blood pressure is the fifth vital of the baseline route. It is charted, not streamed: its bins are the median
  of the charted blood pressure readings of each 15-minute set (nurse or device), read with one more query per patient,
  and it needs no live patch. A ward that charts blood pressure every few hours has a baseline that forms slowly, and it
  is never filled. It feeds no rank, no reason and no model: `scoring.drift` and the student do not read it.
- "The baseline has formed" means formed for all four device vitals. It gates the student and the check.

## 4. The student (`TierCSignal`)

### 4.1 The wired variant

| Variant | Plain file | Inputs | Hidden width | Threshold t | Status |
|---|---|---|---|---|---|
| `all` (with temperature) | `model.json` | 35 | 32 | 0.006 | wired (ruling 1) |
| `no_temp` | `model_no_temp.json` | 27 | 16 | 0.006 | in the repository; never opened by the server |

### 4.2 Notation

For one stay and one vital v, `x_v[i]` is the stored value of v in bin i of the stay's grid, or None for a gap. t is the
current bin. The four vitals are heart rate, respiratory rate, SpO2 and temperature, in that order.

### 4.3 The input vector (35 inputs)

`tier_c/features.py` `student_features` builds it, in this order:

1. For each vital, four numbers: the value, a missing flag, the z against the patient's own baseline, a z-missing flag
   (16 inputs).
2. The NEWS2 total, on oxygen (0 or 1), consciousness not alert (0 or 1) (3 inputs).
3. For each vital, four numbers: the change over 1 hour (against bin t-4) with its missing flag, and the change over 4
   hours (against bin t-16) with its missing flag (16 inputs).

The names are in `model.json` `inputs`. A lag bin that is missing gives 0.0 and sets its flag to 1.0.

### 4.4 The `no_temp` variant

The same definitions without any temperature input (27 inputs). The server does not use it.

### 4.5 Definitions

- Value: the stored bin value, not rounded.
- z: from `baseline_finding` on the series up to and including t. It is signed and not clipped.
- Missing flags: on a bin the server computes, the value-missing flags are always 0.0, and the z-missing flags are 0.0,
  because the baseline has formed.
- NEWS2 total: from the backend scorer, on the current bin's stored inputs. Blood pressure, consciousness, oxygen and the
  SpO2 scale are the newest stored value at or before the bin's end, with no age limit (`services/as_of.py`).
- The consciousness input was constant 0.0 in training (mean 0.0, std 1.0), and its first-layer weights are about
  1e-30. The student ignores it. A patient who is not alert moves p only through the NEWS2 total.
- Never an input: time since admission, patient id, anything about the episode or the outcome, or any other tier's
  verdict.
- The research evaluation scored the forming bins. The server does not. On every bin the server computes, the inputs
  are the research inputs, so the gate can only remove flags.

### 4.6 Standardisation and the forward pass

1. Each feature is computed in double precision and rounded to float32 (`tier_c/float32.py`, ruling 9). A feature beyond the
   float32 range makes that bin not computed.
2. The row must have exactly 35 finite numbers, and a bool is refused.
3. `h0[i] = (x[i] - mean[i]) / std[i]`, clamped to [-1e6, 1e6].
4. Layer 0 is 32 units with ReLU. Layer 1 is one unit with sigmoid. `p` is in [0, 1].

`weights[unit][input]`: the outer index is the output unit. The reader's rules (`parse`): the exact key set; ReLU then
sigmoid only; two layers; layer 1 has one unit; every std above zero; distinct input names; no NaN or Infinity; the file
is at most 1 MiB; the threshold is null or in [0, 1].

### 4.7 The output

- p is the student's probability that this scored bin lies in the onset window of a deterioration episode. It is
  never served: `ModelSignal` carries a state and a provenance, no probability.
- Threshold t = 0.006 (`model.json` `threshold`). The student flags a bin only when `check(p, t, q) == "alert"`.

### 4.8 Numerical tolerance and the parity tests

- `test_plain_mlp.py`: the copy is the research file under its header; the forward pass equals the research values on a
  committed batch; rounding unrounded doubles gives the same p; the wired student has the documented threshold and shape;
  a bad row is refused.
- `test_model_parity.py`: every scored bin of the reference stays matches the research reference exactly, and the gates
  use the reference's `baseline_formed`.
- `test_model_inputs.py`: the reference stay replayed through the ingest routes gives the file's last bin.

### 4.9 What `TierCSignal` receives

```
@dataclass(frozen=True, slots=True)
class TierCSignal:
    probability: Result[float]
    check: Result[Check]   # Check is alert, not_sure or quiet
```

Only `Computed(Check.ALERT)` adds the reason. `not_sure` and `quiet` add nothing. Every `Computed` carries a
`Provenance` and a `Window` (section 8.4).

## 5. The reward policy (`PolicySignal`)

DROPPED (owner decision models-rl-5, 2026-11-16). The policy is not connected. The server does not load it, does not
compute it, and no row shows it. `RowModels` has one field, `prediction`. The arbiter has no policy hook.

It was built under owner decision M-1 (connect it as a flag, so that the deployed demo shows everything). It was removed
after held-out set 2 showed a loss for NEWS2 plus the policy (-0.0127, at the edge). The result stays in the card
and on the Research page. `tier_b/reward_policy.py` stays in the backend as a port. The server uses only its list of
four vital names, `NAMES`. The policy files stay in `models/tier_b/reward_policy/`, and in the manifest.

## 6. The confidence check (`TierCSignal.check`)

### 6.1 The check

`tier_c/confidence.py` `check(p, t, q)`. q None means +infinity.

| Condition | Result | Flag |
|---|---|---|
| `p < t` | `quiet` | no |
| `p >= t` and q is set and `p > q` | `alert` | yes: "Prediction model flag (synthetic, not validated)" |
| `p >= t` and (q is None or `p <= q`) | `not_sure` | no, never |

A NaN p is `not_sure`, never `alert`.

### 6.2 q

The calibration for the wired student (`calibration.json`): q = 0.019854247570037842, t = 0.006, alpha `"0.01"`,
n = 3238, j = 32, k = 3207, draw seed 20261113. It was calibrated on 30-day simulator trials at speed 60, seeds 301 to 340.
It binds the ONNX sha256 `d1ae4e88...ddfdbc`. It is binding (q is at least t).

### 6.3 The file binds one model; refuse it on any difference

The server refuses the file, and then `check` is `NotComputed("calibration not loaded")` on every bin, when any of these
fails (`services/models.py`, `tier_c/confidence.py`):

- The file is at most 64 KiB, and its sha256 equals the text of the `.sha256` sibling (stripped).
- The field set is exactly the 14 fields, with the right types.
- `alpha` is `"0.01"` and `draw_seed` is 20261113.
- `(j, k)` equals `indices(n)`, and q is null exactly when j is 0.
- `variant`, `onnx_sha256` and `t` equal the wired student's.

Retraining or re-exporting the student changes the ONNX sha256 and so invalidates q. A new q needs a new calibration
run. Never copy q to another model.

### 6.4 The certificate

The calibration records the one simulator setting it was made on (30 days, speed 60). Research refuses to claim the
target in any other setting, and a live ward is another setting. So the server never claims coverage. It passes no
`Guarantees` object to the arbiter, and no row or page text makes a coverage claim. q still acts as the stricter bar.

### 6.5 What `not_sure` does in the product

- It shows no flag and adds no reason. Its state is `not_sure` in the row's `models.prediction`.
- It never raises an alert and never moves a patient.
- The absence of a flag is never shown as "safe", "normal" or "low risk".
- It never suppresses a NEWS2 escalation. NEWS2's `escalated`, its reasons and its rank are computed without it.
- `quiet` means only that the student rule did not fire.

### 6.6 What the target means, and what must never be claimed

- Inside the simulator only: for a patient who is not starting to deteriorate, at most 1 in 100 of their 15-minute checks
  shows a supported alert, on average over stays.
- It is not precision. Positives are about 0.3% of bins, so most supported alerts are still false.
- It is not per patient and not per shift. It must never be claimed as a clinical guarantee, and never on a live ward.
- The word "guarantee" never appears on a row or a page.

## 7. How it meets the arbiter

### 7.1 What the arbiter does

`arbitrate` in `domain/arbiter.py`:

1. If NEWS2 abstains, return `NotRanked` naming the missing inputs. Nothing else is looked at, and a NOT RANKED patient
   has no model flag.
2. Rule 1: `escalated` when NEWS2 risk is low-medium, medium or high (a single parameter at 3, a total of 5-6, a total of 7
   or more). The reasons come from NEWS2 only.
3. Rule 3: each significant own-baseline finding adds a reason (`tier_b_finding`) and sets `flagged`. `flagged` orders only
   patients with no trigger and an equal total.
4. Rule 2: `Check.ALERT` adds one reason, "Prediction model flag (synthetic, not validated)", with the rule
   `prediction_model_flag`. It comes after the other reasons.
5. Order (`Ranked.sort_key`): trigger level, then NEWS2 total, then the own-baseline flag (below a trigger only), then
   patient id.

The model flag never sets `escalated` or `flagged` and never enters the sort key. Rule 4 (notes) and `Guarantees` are
typed hooks that change nothing.

### 7.2 Decision table

Every row: order unchanged, and NEWS2's `escalated` and reasons exactly as NEWS2 decides.

| NEWS2 | Student and check | Row's `models.prediction.state` | Reason added |
|---|---|---|---|
| NOT RANKED | not computed ("not ranked") | `not_computed` | none (a `NotRanked` row) |
| scored, baseline forming, or a vital not counted, or stay past 672 bins | not computed, with its reason | `not_computed` | none |
| scored, student or manifest refused | not computed ("student not loaded") | `not_computed` | none |
| scored, calibration refused | p computed, check not computed ("calibration not loaded") | `not_computed` | none |
| scored, formed, p < t | `quiet` | `no_flag` | none |
| scored, formed, t <= p <= q | `not_sure` | `not_sure` | none |
| scored, formed, p > q | `alert` | `flag` | "Prediction model flag (synthetic, not validated)" |

A unit that is not ranked has no model state at all: no score is computed for it.

## 8. Flags, the current bin, series construction, provenance and logging

### 8.1 A flag is the current bin's state

A flag is the state of the current bin, recomputed on every read. Nothing is stored, and `decide` stays pure. The product
opens no alerts and keeps no alert state for the models.

### 8.2 Abstention, and what must never happen

- Not computed, with no flag: a NOT RANKED bin; baseline forming; a stay past 672 bins; a vital not counted; an
  impossible value in the baseline window or a lag bin; a model, calibration or manifest file missing or refused.
- Never: imputing or carrying forward a value; computing on a NOT RANKED bin; reading a later bin; choosing a variant
  by a held-out row; using q with any other ONNX; letting `not_sure` flag; letting a flag change NEWS2's score, risk,
  reasons, `escalated` or the order; a default p, t or q.

### 8.3 The evaluation alert rule (not used by the product)

The research cards count alerts with a 16-bin re-arm rule (`alerts.scan`, `REARM_BINS = 16`): one open alert per
patient, and a new one opens only after 16 bins (4 hours) not alerting. A NOT RANKED bin and a missing bin are not
alerting. The threshold t was chosen under this rule. The product does not use it.

### 8.4 Series construction (ruling 7) and provenance (ruling 8)

The features are built from the backend's own series: `scoring.grid(points, admitted_at, vital)` and
`scoring.counted(state, vital)`. `grid` makes a 15-minute grid from the first stored bin at or after admission, of at most
672 slots. A slot with no row, a null, a non-finite or a physically impossible value is a gap.

(a) Parity. One simulator trial is replayed through the backend path, and the backend's inputs equal the research inputs
for a stay inside the cap with device values (`test_model_parity.py`, `test_model_inputs.py`).

(b) Where the backend and research differ by design. Each is not computed, never approximated:

| Case | Product result |
|---|---|
| A stay past 672 bins. `grid()` keeps the newest 672 slots, so the baseline anchor would move. Scoring the last 672 bins instead was measured on training trials and changes the student's alerts, so that rule was never evaluated | not computed: "stay past 7 days: admission baseline not kept" |
| A physically impossible device value inside the baseline window, or in a lag bin (t-4, t-16). `possible()` makes it a gap, research keeps it | not computed: "impossible value in a lag or baseline bin" |
| A stale patch, or a nurse-typed live value. `counted()` is false. Research has no such rule | not computed |
| The baseline still forming. Research scored these bins with their z-missing flags set | not computed |

Only the current bin is rebuilt from stored inputs (`as_of.news2_as_of`, with `earlier=False`). The reward policy needed
earlier bins, and it is gone.

Provenance. Each computed result carries `source: model`, tier C, the model id (`tier_c/student_vitals` or
`tier_c/confidence_check`), the variant, the file sha256 from the manifest, the manifest record's run id, and the
current bin's start and end. The three file fields are served only for a model value.

### 8.5 Logging

- A load failure at start is one warning line per refused file.
- A failure at score time is logged once per reason per process. No log line carries patient data or a feature value.

## 9. Files

The manifest (`models/manifest.json`, 6,706 bytes at this writing) is the source of every checksum. Do not copy its
numbers into a document.

| Path | Role |
|---|---|
| `models/manifest.json` | the checksums; read at start |
| `models/tier_c/student_vitals/model.json` | student `all`, plain form (34,949 bytes); read at start |
| `models/tier_c/confidence_check/calibration.json` and `.sha256` | q for `all` (660 bytes); read at start |
| `models/tier_c/student_vitals/model.onnx`, `model.mindir`, `model.ms`, `metadata.json` | research record; never loaded |
| `models/tier_c/student_vitals/model_no_temp.json` and its calibration | the `no_temp` variant; never opened |
| `models/tier_b/reward_policy/policy.json` and `no_temperature/policy.json` | the dropped policy; never loaded |

Reference code:

| Code | What it is |
|---|---|
| `research/plain_student.py` | the file `plain_mlp.py` copies |
| `research/student.py` | the feature code that `tier_c/features.py` ports |
| `research/confidence.py` | the check that `tier_c/confidence.py` ports |
| `research/reward_policy.py`, `research/reward_loop.py` | the dropped policy |

No backend dependency was added for the models: the forward pass, the features and the check use the standard library.

## 10. Tests

| Test file | What it checks |
|---|---|
| `tests/unit/test_plain_mlp.py` | the copy; forward-pass parity; float32 rounding; bad rows refused |
| `tests/unit/test_confidence.py` | the check's truth table; the calibration parser; binding |
| `tests/unit/test_models_loader.py` | each refusal gives its fixed reason and one warning; no fall-back to `no_temp`; the real folder loads |
| `tests/unit/test_models_wiring.py` | the model modules import only the standard library and biosentry; the arbiter only receives results |
| `tests/unit/test_model_signals.py` | each not-computed reason; the as-of rule; features |
| `tests/unit/test_model_parity.py` | every scored bin against the research reference |
| `tests/unit/test_model_flags.py` | no model input changes the sort key; the reason is there exactly when the check says alert; `not_sure` is not "no flag" |
| `tests/integration/test_model_flags_api.py` | flags change nothing else; the API starts with an empty models folder; each reason is logged once; an error in the model code leaves the list up |
| `tests/integration/test_model_inputs.py` | stored bins and the batched queries; the reference stay replayed through the ingest routes |
| `tests/unit/test_reward_policy.py` | the dropped policy's port still computes as documented |

## 11. Limits and what was not checked

- The models were not run on a ward hub, a small ARM board or the team server. The forward pass, the feature computation
  and the check were not timed (section 2.5).
- Circular synthetic data: one simulator makes the episodes, the vitals and the judgement. Every number is an upper bound
  inside the simulator, not a performance claim.
- The research evaluation scored the forming bins. The product does not. The cards' numbers include those bins.
- The student never saw a non-alert consciousness level in training (section 4.5).
- Validation and held-out facts are in the cards.

## Rulings (orchestrator, 2026-11-13, by the owner's delegation M-2)

Owner decisions that shaped the wiring:

- H-1: the models run inside the API process on the team server.
- M-1: the alert policy was connected as a flag. Superseded by models-rl-5, which dropped it.
- M-2: the wiring decisions are delegated to the orchestrator inside fixed rules, and each is written down.
- models-rl-5: do not connect the reward policy.

Each ruling, and where it stands in the code:

1. Wire the with-temperature files (`all`). `models.WIRED_VARIANT = "all"`. The `no_temp` files are never opened.
   Temperature comes from the stored bins, read with one extra query for the whole set of patients.
2. A NOT RANKED bin gives `NotComputed("not ranked")`. Done (`model_signals.eligible`).
3. The per-bin check result is a field of `TierCSignal`. Done (`check`).
4. No coverage claim on a live ward. The server passes no `Guarantees` object, and q still applies (section 6.4).
5. A flag is the current bin's state, recomputed on read. Nothing is stored. Done.
6. "Current" is the last stored bin. Only that bin is rebuilt from stored inputs, since the policy is gone.
7. Series from `grid()` and `counted()`, not computed past 672 bins, with a parity test. Done (section 8.4).
8. Provenance carries the model id, variant, file sha256, run id, and the bin's start and end. Done.
9. float32 rounding before the forward pass. Done (`float32.py`).
10. Not computed until the baseline has formed for all four device vitals. Done (`features.formed`).
11. The row text is "Prediction model flag (synthetic, not validated)", with its own rule `prediction_model_flag`. The
    policy's text went with the policy. The word "guarantee" never appears.
12. A flag adds a reason and changes no order, and never sets `escalated` or `flagged`. Done, and the arbiter's docstring
    says so.
13. The files are copied into the image and verified against the manifest at start. A mismatch means not computed and
    one warning.
14. A refused calibration gives no flag. `probability` stays computed, and `check` is `NotComputed("calibration not
    loaded")`.
15. A failure at score time is logged once per reason per process. The per-reason count that the ruling asked for was not
    built.
16. An earlier impossible value: in the code, the student is not computed when such a value lies in the baseline window
    or in a lag bin. This is stricter than the ruling, which let the baseline skip it.
17. The 12-hour rule for earlier nurse-typed bins belonged to the policy. It is moot now that only the current bin is
    rebuilt.

Three gaps were found before the wiring and are closed: no hook carried the per-bin check result (ruling 3); the
calibration records one simulator setting (section 6.4); the backend builds the series differently from research
(section 8.4).

Facts that stay in the cards and on the Analytics page: the held-out results above. No gain beyond chance is shown for
the student or the policy added to NEWS2. Only the student with the check shows a small gain on held-out set 2.
