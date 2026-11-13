# Wiring the three research models into the arbiter: a hand-off

This file says how to RUN the shipped files. It wires nothing. The wiring slice can follow it without asking
the models session anything. Where this file and the code disagree, the code is right; the files named here are
the sources of truth. The wiring choices are settled by the rulings at the end of this file; every section below
already follows them.

## 1. Scope and status

Three offline research models:

| Model | Folder | What it gives per scored bin | Arbiter hook |
|---|---|---|---|
| Distilled vitals student | `models/tier_c/student_vitals/` | a probability p | `TierCSignal.probability: Result[float]` |
| Confidence check on the student | `models/tier_c/confidence_check/` | `alert`, `not_sure` or `quiet`, from p | a new field on `TierCSignal` (ruling 3); `Guarantees.conformal_certificate_current` (ruling 4) |

Each model ships in two variants, with and without temperature. Exactly one variant of all three is wired; ruling 1
says which, from what the backend stores, never from a held-out row. Both variants are documented in full here.
| Reward-driven alerting policy | `models/tier_b/reward_policy/` | alerting or not (a bool) | `PolicySignal.alert: Result[bool]` |

- All three are research only. They were trained and judged on synthetic simulator trials. They are not
  validated. No claim about real patients is made.
- Held-out results are in the cards and their held-out files: `models/tier_c/student_vitals/card.md` and
  `held_out.json`, `models/tier_b/reward_policy/card.md` and `held_out.txt`, `models/tier_c/confidence_check/card.md`.
  This file cites only the facts line of the rulings section.
- Nothing here changes NEWS2. NEWS2 is never learned, re-weighted or overridden. NEWS2 stays the only thing that
  sets `escalated` and the only model-free input to the order.
- Fixed rules (owner decision M-2): a model may flag and never outranks or lowers a NEWS2 trigger; `not_sure`
  shows as no flag and is recorded; a missing or bad model file means not computed, never a default.
- Today every one of these hooks is inert in `backend/src/biosentry/domain/arbiter.py`: the arbiter accepts it,
  records its name in `Ranked.inert` or `NotRanked.inert`, and changes nothing. The wiring slice makes the
  student and the policy add a reason (a flag) to a ranked row, and nothing else (rulings 3, 11, 12).

## 2. The chosen path: in-process, inside the API (owner decision H-1)

When connected, the student, the reward policy and the confidence check run INSIDE the API process on the team
server. There is no model service and no network call per score. No Huawei Cloud account is needed for it. No new
backend dependency is added. Every later section describes this path.

### 2.1 What the API image holds and loads, once, at start

The files are copied into the API image at build and verified at start (ruling 13). The image holds the wired
variant (ruling 1) and the manifest, nothing else:

| File | With temperature (wired unless the backend keeps no temperature history) | No temperature (wired only if the backend keeps no temperature history) |
|---|---|---|
| Student, plain JSON weights, format `biosentry-plain-mlp-1` | `models/tier_c/student_vitals/model.json` (`all`) | `models/tier_c/student_vitals/model_no_temp.json` (`no_temp`) |
| q and its bindings, for that student | `models/tier_c/confidence_check/calibration.json` and its `.sha256` | `models/tier_c/confidence_check/calibration_no_temp.json` and its `.sha256` |
| The policy's six numbers and config | `models/tier_b/reward_policy/policy.json` | `models/tier_b/reward_policy/no_temperature/policy.json` |
| Checksums | `models/manifest.json` | `models/manifest.json` |

The three model files always come from one column. What the backend stores today: `repositories/postgres.py`
`HISTORY_VITALS = ("heart_rate", "respiratory_rate", "spo2")`, the vitals whose full history reaches scoring as
`vital_history`. Temperature is in `DEVICE_VITALS` and its bins are read only for the 12-bin sparkline
(`TREND_BINS = 12`); the seed (`db/seed.py`) writes only its last 12 bins. The dashboard session checks whether
temperature history exists in the backend at all; if it does not, the no-temperature column is wired, for that
reason only.

Nothing else. The API does not load the ONNX, MindIR or `.ms` files, and it does not import onnxruntime,
MindSpore or numpy. The ONNX sha256 is needed only as a value: it is inside `model.json` (`source.onnx_sha256`) and
inside the calibration file, and the two are compared.

The API never picks a variant by its held-out row, never loads files of two variants, and never switches files
per row.

### 2.2 The checksum rule

At start, before any use, for each loaded file:

1. Read the bytes once, with the size cap of its reader: `model.json` 1 MiB (`plain_student.MAX_BYTES`), policy
   1 MiB (`reward_loop.MAX_FILE_BYTES`), calibration 64 KiB (`confidence.MAX_BYTES`).
2. Find the record with the same `path` in `models/manifest.json` `models`. Its `bytes` must equal the byte count
   and its `sha256` must equal the sha256 of the bytes. No record means refused.
3. Then the file's own checks:
   - Student: the `plain_student.parse` rules (section 4.6).
   - Calibration: its sha256 must equal the text of its `.sha256` sibling, and every check of section 6.3 must
     pass, including: `onnx_sha256` equals `model.json` `source.onnx_sha256`; `t` equals `model.json`
     `threshold`; `alpha` is `"0.01"`; `draw_seed` is 20261113; `variant` equals the student file's `variant`
     (`all` or `no_temp`).
   - Policy: its content sha256 as `research/reward_loop.py` `load_policy` checks it (section 5.1): the sha256 of
     `json.dumps(body, sort_keys=True, separators=(",", ":"))`, where body is the object without `"sha256"`, must
     equal the file's `"sha256"`; plus the `temperature`, `Params` and `config_sha256` checks. ("`model.json`"
     in this list means the wired student file.)

Current values, read from `models/manifest.json` in this worktree on 2026-11-13 and recomputed with
`shasum -a 256`. This table is a snapshot. `python -m research.manifest --write` regenerates the manifest when a file
changes; the manifest is the source, not this table.

| Path | Bytes | sha256 (manifest and recomputed) | Run id (manifest) | Other value checked |
|---|---|---|---|---|
| `models/tier_c/student_vitals/model.json` | 34949 | `d05d75ee0ad8da0db138a74f3a9796c98de6b8af5dfea513789d5521f1768af6` | `m1-20261113-3d634f3` | `source.onnx_sha256` `d1ae4e881732922024b08c6cf9c37adbd8b01c48bc8390ee22b28ab802ddfdbc`, threshold 0.006 |
| `models/tier_c/confidence_check/calibration.json` | 660 | `7815bd3885c794fafa38e8384d6837300c163738dbb6a0a7a126c4dca2484f11` | `m3-20261113-fbd39f2` | `.sha256` sibling holds the same value; `onnx_sha256` `d1ae4e88...ddfdbc`, t 0.006, alpha "0.01", variant `all` |
| `models/tier_b/reward_policy/policy.json` | 5064 | `21cf038e4ab699906eeab87d56ddbd39965915a8ec085e75f56ca99501114775` | `m2-20261113` | content sha256 `b364a680b989a659ae747f929806a65a9f828a0e412ba89d35ca72dd840f804f`, `temperature` true |
| `models/tier_c/student_vitals/model_no_temp.json` | 14903 | `d041b6584edc7d67c54dcdb03e81f0c1a58f51282a3cf02fbc3e869ff53cb2fc` | `m1-20261113-3d634f3` | `source.onnx_sha256` `e6b6ae44c11aa38fc38a961eebaf3e3bd51b1b5812b9e200d31d8c04101869ee`, threshold 0.006 |
| `models/tier_c/confidence_check/calibration_no_temp.json` | 664 | `4a3de9544cce7e6cc838837bb2c1646261d85d1f41bef35f84135509fa0729bb` | `m3-20261113-fbd39f2` | `.sha256` sibling holds the same value; `onnx_sha256` `e6b6ae44...69ee`, t 0.006, alpha "0.01", variant `no_temp` |
| `models/tier_b/reward_policy/no_temperature/policy.json` | 4916 | `522195aa8104271d3bfec0e87ab1f37e07b074dccbd5a347d05191905a12a3b3` | `m2-20261113` | content sha256 `ef60e564de8e3176d87fe78eabc3fc366e6b802ff0a9034e46122dc78090e020`, `temperature` false, temperature weight 0.0 |

The first three rows are the with-temperature variant, the last three the no-temperature variant; only the wired
variant's three are checked and loaded (ruling 1).

The `.sha256` siblings are records, not model files, so the manifest has no entry for them; each is checked against
its calibration file's bytes. The manifest has no checksum of itself (snapshot: 6706 bytes, sha256
`20b3b81aeb9d327f49bb6f5edfbc1749757ecd01524f4aca5d26ee1aec1dfe76`). A person who changes a file and the manifest
together is not detected; the review of the pull request is the guard.

### 2.3 Failure behaviour

A mismatch or any other load failure means not computed and ONE warning line at start (ruling 13). The signal's
hook gets `NotComputed(reason)` with one plain reason string (`domain/computed.py`: `NotComputed` is a frozen
dataclass with one field, `reason: str`; `Result[T]` is `Computed[T] | NotComputed`). There is never a default
probability, a default threshold, a default q, or a fall-back to the other variant. The API keeps serving NEWS2
unchanged. No flag is shown for a not-computed signal.

`check` below is the new `TierCSignal` field of ruling 3 (section 4.9).

| Case | `TierCSignal.probability` | `TierCSignal.check` | `PolicySignal.alert` |
|---|---|---|---|
| Student file missing, unreadable, over its cap, wrong sha256, no manifest record, wrong `format`, unknown keys, a non-finite number | `NotComputed("student not loaded")` | `NotComputed("student not loaded")` (nothing to check) | unaffected |
| Calibration or its `.sha256` missing, unreadable, over its cap, wrong sha256, or a binding mismatch (`onnx_sha256`, t, alpha, draw seed, variant) | `Computed(p)` | `NotComputed("calibration not loaded")`: no flag (ruling 14) | unaffected |
| Policy file missing, unreadable, over its cap, wrong file or content sha256, wrong `temperature` flag, bad params or config | unaffected | unaffected | `NotComputed("policy not loaded")` |
| An input vector of the wrong length, or a non-finite or bool feature, at score time | `NotComputed(<fixed reason>)` for that bin | `NotComputed(<same reason>)` | unaffected |
| A NOT RANKED bin (ruling 2) | `NotComputed("not ranked")` | `NotComputed("not ranked")` | `NotComputed("not ranked")` |
| Baseline still forming (ruling 10) | `NotComputed("baseline forming")` | `NotComputed("baseline forming")` | `NotComputed("baseline forming")` |
| Stay past 672 bins (ruling 7) | `NotComputed("stay past 672 bins")` | same | same |
| An earlier bin's scored status cannot be rebuilt from stored inputs (ruling 6) | unaffected | unaffected | `NotComputed("earlier bin not stored")` |

- Load failures are found once, at start: one warning line per failed file with the file and the reason. The load
  result is fixed for the life of the process; a new file needs a new image and a restart.
- A failure found at score time is logged once per reason per process, with a count (ruling 15; section 8.5).
- The log line never carries patient data.

### 2.4 The forward pass in the API

- The reference is `research/plain_student.py` (`load`, `parse`, `forward`): standard library only (`json`, `math`,
  `dataclasses`), about 100 lines of logic.
- The layering, as the repository stands: `research/` imports the backend (`biosentry.domain...`), and no file under
  `backend/` imports `research/` (checked with grep). The backend must not start to import it. So the code lives in
  the backend: either a port, or a copy of the file vendored under `backend/src/biosentry/` (for example next to
  the scoring tiers). Both are consistent with the layering. A verbatim copy is the smaller change and gives exact
  equality.
- Either way the backend's code must pass the fixed-batch parity test (section 4.8, and section 10 item 1).
- The feature code (`stay_features`, `patient_features`) is numpy in research: it must be ported to plain Python
  on the backend's own `baseline_finding`, never imported.

### 2.5 Time per score (machine-dependent)

Measured on this project's Docker image on an Apple Silicon Mac (Python 3.11.18, aarch64, one process, 20,000
calls after 2,000 warm-up calls, a plain valid row). Figures as reported for this hand-off; they depend on the
machine and its load. Only the wired variant's column applies (ruling 1).

| | `model.json` (35 inputs) | `model_no_temp.json` (27 inputs) |
|---|---|---|
| Pure-Python `forward`, median per call | 81.0 microseconds | 47.1 microseconds |
| Pure-Python `forward`, 99th percentile (busy shared machine) | 1,332.5 microseconds | 195.3 microseconds |
| Load and validate the JSON, once | 2.92 ms | 1.20 ms |
| For comparison: the ONNX file through onnxruntime, median (`timing.json`) | 8.58 microseconds | 8.37 microseconds |

So a ward of about 13 patients needs about 1 millisecond of model time per 15-minute bin (13 x 81.0 microseconds).

Not measured: the feature computation per patient (the baseline z calls `baseline_finding` over the stay's
history for each vital), the policy's state machine, the confidence check, and anything on the team server's own
hardware.

### 2.6 What "no Huawei Cloud account needed" means here

Scoring is local arithmetic in the API process: the student's two small layers, the policy's weighted sum and the
check's comparison. Training (MindSpore) and the MindIR and MindSpore Lite `.ms` files belong to the research side
and to a possible ward hub. The in-process path does not use them, and nothing is sent to a cloud service.

## 3. The bin, the stay and the history the features need

- Bin: one 15-minute bin per patient (`research/trials.py` `BIN_MINUTES = 15`; backend
  `services/scoring.py` `BIN = timedelta(minutes=15)`). Each vital has at most one stored value per bin (the
  simulator's 15-minute median for heart rate, respiratory rate and SpO2). A bin with no row, or a null value, is
  a gap. Nothing is filled.
- Minute convention in the research code: a trial row carries `minute`, a multiple of 15, with
  `0 < minute < days * 1440`. A stay's bins run from its first row to its last row, every 15 minutes
  (`range(min(rows), max(rows) + 1, BIN_MINUTES)`). The code only ever uses differences of bins, never clock
  time. Whether the simulator's minute labels a bin's start or its end is defined in the simulator repo
  (`docs/trial-format.md`), not here. The backend stores a bin at its start time (`schemas/baseline.py`: the bin's
  end is `latest_at + 15 min`). The wiring needs one rule, used everywhere: bin i is the i-th 15-minute slot of the
  stay's grid from `grid()` (ruling 7).
- Current bin (ruling 6): the last completed stored 15-minute bin, with that bin's stored inputs. The arbiter scores
  NEWS2 on the live vitals, so a flag can lag NEWS2 by up to 15 minutes.
- Scored bin: a bin whose stored inputs give a `News2Score` from `score_news2(...)` (all eight NEWS2 inputs present
  and possible), so the arbiter would rank it. A NOT RANKED bin is one where NEWS2 returns `News2Abstained`. In the
  research data `Row.ranked` is this flag, and `research/rescore.py` `check_parity` refuses a trial where it differs
  from the backend's own `score_news2`.
- Consequence: on a scored bin all four device vitals (heart rate, respiratory rate, SpO2, temperature) are
  present, because NEWS2 needs them. A scored bin never has a missing device vital. The value-missing flags are
  therefore always 0 on scored bins (and were constant 0 in training, section 4.5). With temperature missing in a
  bin, NEWS2 abstains, so the bin is NOT RANKED and nothing is computed; a temperature gap in an earlier bin only
  sets that lag's missing flag, which is what the trained model does (ruling 1). Files are never switched per row.
- Stay: one patient's admission, from the first bin to the last. All history below is of the same stay only.
- History the features need, with the constants from the code:

| Need | Constant | Source |
|---|---|---|
| Own baseline window | the first 24 valid bins of the stay, then frozen | `baseline.py` `DEFAULTS = BaselineParams()`, `window_bins = 24` |
| "Forming" period | z is not computed until the stay has 24 valid values of that vital, counting the current bin (6 hours with no gaps) | `baseline_finding` returns `NotComputed("baseline forming, n of 24 bins")` |
| 1-hour lag | 4 bins earlier | `student.py` `LAG_1H = 4` |
| 4-hour lag | 16 bins earlier | `student.py` `LAG_4H = 16` |
| Policy persistence | the last p bins (p = 5 with temperature, 3 without), each scored, with no gap | the policy file's `params.p` |
| Cap on the stay | 672 bins (7 days); past it, not computed | backend `services/scoring.py` `MAX_BINS` (ruling 7) |
| Alert re-arm | 16 bins not alerting: an evaluation convention only, not used by the product | `alerts.py` `REARM_BINS = 16` (ruling 5) |

- A valid bin for the baseline is a value that is not None, not a bool and finite (`baseline._valid`). Values of
  NOT RANKED bins count: they feed the baseline and the lags even though the bin itself is not scored.
- So the feature code needs the stay's grid for each device vital from `grid()` (the baseline anchor is the first
  24 valid bins), the current bin's stored NEWS2 inputs, and, for the policy only, the scored status of the p - 1
  bins before the current one, rebuilt on read from their stored inputs (ruling 6). Scores are never stored; nothing
  is stored between reads (ruling 5).
- "The baseline has formed" (ruling 10) means formed for every vital in `HISTORY_VITALS` (today heart rate,
  respiratory rate and SpO2). It gates both the student and the policy.

## 4. The student (`TierCSignal`)

### 4.1 Variants

| Variant | Plain file | ONNX file (sha256 binding) | Inputs | Hidden width | Parameters | Threshold t | Status |
|---|---|---|---|---|---|---|---|
| `all` (with temperature) | `model.json` | `model.onnx` | 35 | 32 | 1,185 | 0.006 | the wired variant (ruling 1) unless the backend keeps no temperature history |
| `no_temp` | `model_no_temp.json` | `model_no_temp.onnx` | 27 | 16 | 465 | 0.006 | the wired variant (ruling 1) only if the backend keeps no temperature history |

Both shipped students are `inputs: "trend"` (base plus trend), distilled, loss MSE on the teacher's logits
(`metadata.json` `variants.*.student`). The input names below were printed from `metadata.json`
`features_student`, from `model.json` `inputs`, and from `research.student._names("trend", temp)`; all three are
identical, in this order.

### 4.2 Notation

For one stay and one vital v, `x_v[i]` is the stored value of v in bin i of the stay's grid (i = 0 is the first
slot), or None for a gap. t is the current, scored bin. `series_v = [x_v[0], ..., x_v[t]]`, with None in every gap
and with the values of NOT RANKED bins included. `B_v = baseline_finding(v, series_v, DEFAULTS)` (backend
`domain/scoring/tier_b/baseline.py`). `S = score_news2(stored inputs at bin t)`, a `News2Score` because t is scored.

### 4.3 The input vector, variant `all` (35 inputs)

Ruling 10 applies first: while `B_v` of any vital in `HISTORY_VITALS` is still forming, nothing below is computed
(section 4.9). This variant is wired only when temperature history is kept (ruling 1), and then `HISTORY_VITALS`
must include temperature; on a computed bin the z-missing flags are therefore 0.

| i | Name | Formula at scored bin t | Units | Missing rule | Bins read |
|---|---|---|---|---|---|
| 0 | `heart_rate` | `x_hr[t]` as stored (not rounded) | /min | None gives 0.0 (cannot happen on a scored bin) | t |
| 1 | `heart_rate_missing` | 1.0 if `x_hr[t]` is None, else 0.0 | flag | always 0.0 on a scored bin | t |
| 2 | `heart_rate_z` | `B_hr[1].z` when `B_hr` is a tuple, else 0.0 | sd | 0.0 while forming (not computed in the product) | 0..t |
| 3 | `heart_rate_z_missing` | 0.0 when `B_hr` is a tuple, else 1.0 | flag | 1.0 while forming (not computed in the product) | 0..t |
| 4 | `respiratory_rate` | `x_rr[t]` | /min | as i 0 | t |
| 5 | `respiratory_rate_missing` | as i 1 | flag | as i 1 | t |
| 6 | `respiratory_rate_z` | `B_rr[1].z` or 0.0 | sd | as i 2 | 0..t |
| 7 | `respiratory_rate_z_missing` | as i 3 | flag | as i 3 | 0..t |
| 8 | `spo2` | `x_spo2[t]` | % | as i 0 | t |
| 9 | `spo2_missing` | as i 1 | flag | as i 1 | t |
| 10 | `spo2_z` | `B_spo2[1].z` or 0.0 | sd | as i 2 | 0..t |
| 11 | `spo2_z_missing` | as i 3 | flag | as i 3 | 0..t |
| 12 | `temperature` | `x_temp[t]` | °C | as i 0 | t |
| 13 | `temperature_missing` | as i 1 | flag | as i 1 | t |
| 14 | `temperature_z` | `B_temp[1].z` or 0.0 | sd | as i 2 | 0..t |
| 15 | `temperature_z_missing` | as i 3 | flag | as i 3 | 0..t |
| 16 | `news2_total` | `S.total` (0 to 20) | points | never missing on a scored bin | t |
| 17 | `on_oxygen` | 1.0 if supplemental oxygen is recorded true, else 0.0 | flag | never missing on a scored bin | t |
| 18 | `consciousness_not_alert` | 1.0 if ACVPU is not `A`, else 0.0 | flag | never missing on a scored bin | t |
| 19 | `heart_rate_d1h` | `x_hr[t] - x_hr[t-4]` | /min | 0.0 if t - 4 < 0 or `x_hr[t-4]` is None | t, t-4 |
| 20 | `heart_rate_d1h_missing` | 1.0 in the missing case of i 19, else 0.0 | flag | | t-4 |
| 21 | `heart_rate_d4h` | `x_hr[t] - x_hr[t-16]` | /min | 0.0 if t - 16 < 0 or `x_hr[t-16]` is None | t, t-16 |
| 22 | `heart_rate_d4h_missing` | 1.0 in the missing case of i 21, else 0.0 | flag | | t-16 |
| 23 | `respiratory_rate_d1h` | `x_rr[t] - x_rr[t-4]` | /min | as i 19 | t, t-4 |
| 24 | `respiratory_rate_d1h_missing` | as i 20 | flag | | t-4 |
| 25 | `respiratory_rate_d4h` | `x_rr[t] - x_rr[t-16]` | /min | as i 21 | t, t-16 |
| 26 | `respiratory_rate_d4h_missing` | as i 22 | flag | | t-16 |
| 27 | `spo2_d1h` | `x_spo2[t] - x_spo2[t-4]` | % | as i 19 | t, t-4 |
| 28 | `spo2_d1h_missing` | as i 20 | flag | | t-4 |
| 29 | `spo2_d4h` | `x_spo2[t] - x_spo2[t-16]` | % | as i 21 | t, t-16 |
| 30 | `spo2_d4h_missing` | as i 22 | flag | | t-16 |
| 31 | `temperature_d1h` | `x_temp[t] - x_temp[t-4]` | °C | as i 19 | t, t-4 |
| 32 | `temperature_d1h_missing` | as i 20 | flag | | t-4 |
| 33 | `temperature_d4h` | `x_temp[t] - x_temp[t-16]` | °C | as i 21 | t, t-16 |
| 34 | `temperature_d4h_missing` | as i 22 | flag | | t-16 |

### 4.4 The input vector, variant `no_temp` (27 inputs)

The wired variant only if the backend keeps no temperature history (ruling 1). The same definitions without any
temperature input, and with one changed NEWS2 input. Ruling 10 gates it on the vitals in `HISTORY_VITALS` (today
heart rate, respiratory rate and SpO2, exactly this variant's three):

| i | Name | i | Name | i | Name |
|---|---|---|---|---|---|
| 0 | `heart_rate` | 9 | `spo2_missing` | 18 | `heart_rate_d4h_missing` |
| 1 | `heart_rate_missing` | 10 | `spo2_z` | 19 | `respiratory_rate_d1h` |
| 2 | `heart_rate_z` | 11 | `spo2_z_missing` | 20 | `respiratory_rate_d1h_missing` |
| 3 | `heart_rate_z_missing` | 12 | `news2_total_minus_its_temperature_points` | 21 | `respiratory_rate_d4h` |
| 4 | `respiratory_rate` | 13 | `on_oxygen` | 22 | `respiratory_rate_d4h_missing` |
| 5 | `respiratory_rate_missing` | 14 | `consciousness_not_alert` | 23 | `spo2_d1h` |
| 6 | `respiratory_rate_z` | 15 | `heart_rate_d1h` | 24 | `spo2_d1h_missing` |
| 7 | `respiratory_rate_z_missing` | 16 | `heart_rate_d1h_missing` | 25 | `spo2_d4h` |
| 8 | `spo2` | 17 | `heart_rate_d4h` | 26 | `spo2_d4h_missing` |

- `news2_total_minus_its_temperature_points` = `S.total - sum(p.score for p in S.parameters if p.parameter ==
  "temperature")`. The temperature points are 0 to 3 from `tables.TEMPERATURE` (≤35.0: 3; 35.1-36.0: 1;
  36.1-38.0: 0; 38.1-39.0: 1; ≥39.1: 2).
- A bin is still scored only when NEWS2 has a temperature for that bin. "No temperature" means temperature does not
  feed the model; the current bin's stored temperature still decides whether NEWS2 scores the bin.

### 4.5 Exact definitions of each family (from `research/student.py`)

- Value: `float(x_v[t])`, the stored bin value, not rounded. NEWS2 rounds for banding; the features do not.
- Value-missing flag: 1.0 when the value is None. On a scored bin this cannot happen. In training these columns
  were constant 0.0, so `model.json` stores mean 0.0 and std 1.0 for them, and their first-layer weights are about
  1e-30: they have no effect on p.
- z: `patient_features` calls `baseline_finding(v, series_v, DEFAULTS)` with the series up to and including t
  (gaps None, NOT RANKED values included). On a tuple it takes `Deviation.z = (x_v[t] - median) / spread`, where
  median is the median of the first 24 valid bins, `spread = max(1.4826 * MAD, floor_v)`, floors heart rate 2.0,
  respiratory rate 1.0, SpO2 1.0, temperature 0.1. This z is SIGNED and not clipped (the policy's z in section 5 is
  clipped; do not reuse one for the other). On `NotComputed` the research code gives 0.0 and sets the z-missing
  flag; the product does not compute the bin at all (ruling 10).
- Ruling 10 differs from the research evaluation: research scored the forming bins with their z-missing flags set,
  and its validation and held-out numbers include them. The product only removes those bins; on every bin it
  does compute, the inputs are exactly the research inputs, so the ruling can only remove flags, never change a
  value.
- NEWS2 total: `S.total` from the backend scorer on the bin's stored inputs (`research/rescore.score_row` builds
  them as the backend does).
- On oxygen: `float(supplemental_o2)`.
- Consciousness not A: `float(consciousness != "A")`. In training this column was constant 0.0 (no training bin
  had ACVPU other than A): mean 0.0, std 1.0, weights about 1e-30. The student therefore ignores this input; a
  patient who is not alert moves p only through the NEWS2 total.
- d1h and d4h (`stay_features`): `cur - lag`, with the lag bin 4 or 16 bins earlier on the stay's 15-minute grid.
  The lag bin is ANY stored bin of the same stay with a value, scored or NOT RANKED. A lag bin that has no row,
  has a null value, or falls before the stay's first bin is missing: the value is 0.0 and the flag 1.0. The
  current value is always present on a scored bin. The NEWS2 status of the lag bin is never looked at.
- Never an input: time since admission, patient id, anything about the episode, the outcome.

### 4.6 Standardisation and the forward pass

Step 0, REQUIRED (ruling 9): compute each feature in double precision, then round it to float32 before the
forward pass, with the standard library: `struct.unpack("f", struct.pack("f", x))[0]`. This is what research does
(`Scorer.__call__`: `view(x.astype(np.float32), ...)`), and the ONNX graph takes float32. A feature beyond the
float32 range makes `struct.pack` raise `OverflowError`: that bin is not computed.

From `research/plain_student.py` (`load`, `parse`, `forward`), format `"biosentry-plain-mlp-1"`:

1. Check the row has exactly n values (35 for `all`, 27 for `no_temp`), each an `int` or `float` and finite. A bool is refused
   (`type(v) not in (int, float)`), so pass flags as 0.0 and 1.0, never True and False.
2. `h0[i] = (x[i] - mean[i]) / std[i]`, then clamp to [-1e6, 1e6] (`CLAMP`).
3. Layer 0, for each hidden unit u: `acc = 0.0; for i: acc += h0[i] * W0[u][i]`; then `a[u] = max(acc + b0[u], 0.0)`
   (ReLU).
4. Layer 1: `acc = 0.0; for u: acc += a[u] * W1[0][u]`; `z = acc + b1[0]`.
5. `p = sigmoid(z)`, computed as `1 / (1 + exp(-z))` for z >= 0 and `exp(z) / (1 + exp(z))` for z < 0, then
   clamped to [0, 1].

Orientation: `weights[unit][input]`. The outer index is the output unit and the inner index is the input. This is
the transpose of the ONNX initialiser (`[input][unit]`). Layer 0 is 32 x 35, layer 1 is 1 x 32 (`no_temp`: 16 x 27
and 1 x 16). `mean` and `std` are the training rows' column moments (seeds 61-160); a zero std became 1.0. Every
number is a float32 value written with Python's `repr`. The arithmetic is double precision in the ONNX graph's
order (Sub, Div, Gemm, Relu, Gemm, Sigmoid).

Reader rules to keep in a port (`parse`): exact key set; `format`, `hidden_activation == "relu"`,
`output == "sigmoid"`; exactly two layers; layer 1 has one unit; every std above 0; distinct input names; no NaN or
Infinity; file at most 1 MiB; threshold null or in [0, 1].

### 4.7 The output and the label

- p is the student's probability that this scored bin lies in the onset window of a deterioration episode.
- Label (`patient_labels`): 1 from `episode_start` to fully in (`episode_start + ramp_minutes`), both ends
  included, on the bin's minute; bins after fully in are excluded from training (-1); patients present at the
  trial start (admit minute 0) are excluded (-2); 0 otherwise. `outcome` is never a label.
- Threshold t = 0.006 for both variants (the student file's `threshold`, `metadata.json`
  `variants.<all|no_temp>.thresholds.student.threshold`). It
  is the smallest t in the grid 0.001 to 0.009 (step 0.001), then 0.01 to 0.99 (step 0.01) whose alerts AND time in
  alert on validation seeds 261-300, under the evaluation alert rule of section 8.3 with "alerting when p >= t",
  are both at most the NEWS2 chart trigger's. It was computed from the ONNX output.
- The student flags a bin only when `check(p, t, q) == "alert"` (section 6.1, ruling 3).

### 4.8 Numerical tolerance and the port's parity test

- Research tolerance (README "Plain export", `tests_ml/test_export_plain.py`): on a fixed batch of 1,000 rows the
  plain forward pass is within 1e-6 of onnxruntime and within 1e-5 of MindSpore and MindIR. That test runs in the
  research ML gate, not in the backend.
- Parity test for the backend port, with no numpy:
  1. Once, in the research environment (standard library only), build a fixed batch from each student file
     (`model.json` and `model_no_temp.json`, so the test holds whichever variant ruling 1 wires):
     `rng = random.Random(7)`; rows 0-799: `x[i] = mean[i] + rng.uniform(-4, 4) * std[i]`; rows 800-999: the same,
     then each value replaced, with probability 0.5, by 0.0 or 1.0 at random (this mirrors the research batch,
     which also mixes flags). Round every value with `struct.unpack("f", struct.pack("f", x))[0]` (ruling 9).
  2. Expected output: `research.plain_student.forward(research.plain_student.load(path), row)` per row.
  3. Commit `{"model_sha256": <sha256 of the file>, "rows": [...], "p": [...]}` per file as a backend test fixture.
  4. The backend test loads each committed student file through the port, asserts its sha256 equals the fixture's,
     and asserts `abs(p_port - p_expected) <= 1e-12` for every row. A verbatim copy of `forward` gives exact
     equality; keep the tolerance only for a port that reorders nothing but differs in the last bit.
  5. A second case feeds the same rows UNROUNDED (doubles near, but not on, float32 values) through the backend's
     own rounding step and asserts the same p: this shows the rounding step is in the path.
  6. Also assert the threshold read from the file is exactly 0.006, and that a row of the wrong length, a NaN, an
     infinity and a bool are each refused.

### 4.9 What `TierCSignal` receives

The wiring slice ADDS one field to the arbiter's `TierCSignal` (ruling 3). Proposal:

```
@dataclass(frozen=True, slots=True)
class TierCSignal:
    probability: Result[float]
    check: Result[Literal["alert", "not_sure", "quiet"]]   # name proposed; the exact name is the wiring slice's
```

`check` holds `check(p, t, q)` from `research/confidence.py`, spelt with an underscore (`"not sure"` there becomes
`"not_sure"`). Only `Computed("alert")` flags; `Computed("not_sure")` is recorded in the decision and shows no flag.

| Case | `probability` | `check` | Flag on the row |
|---|---|---|---|
| NOT RANKED bin (ruling 2) | `NotComputed("not ranked")` | `NotComputed("not ranked")` | none |
| Scored bin, the baseline of any vital in `HISTORY_VITALS` forming (ruling 10) | `NotComputed("baseline forming")` | `NotComputed("baseline forming")` | none |
| Stay past 672 bins, or a vital not `counted()` (ruling 7) | `NotComputed(<reason>)` | `NotComputed(<same reason>)` | none |
| Student file failed its checks (ruling 13) | `NotComputed("student not loaded")` | `NotComputed("student not loaded")` | none |
| Calibration failed or missing, student fine (ruling 14) | `Computed(p)` | `NotComputed("calibration not loaded")` | none |
| Computed, p < t | `Computed(p)` | `Computed("quiet")` | none |
| Computed, t <= p <= q | `Computed(p)` | `Computed("not_sure")` | none; recorded |
| Computed, p > q (so p >= t) | `Computed(p)` | `Computed("alert")` | "Prediction model flag (synthetic, not validated)" |
| Scored bin, a lag bin missing (formed baseline) | `Computed(p)`, d1h or d4h value 0.0 and flag 1.0 | from that p | as the rows above |
| Feature computation fails (an internal error) | `NotComputed(<fixed reason>)`; never a default p | `NotComputed(<same reason>)` | none |

`Computed` needs a `Provenance` and a `Window` (`domain/computed.py`); section 8.4 gives the values (ruling 8).

## 5. The reward policy (`PolicySignal`)

The policy IS connected, as a flag (owner decision M-1). It can add a reason to a ward row; it never outranks or
lowers a NEWS2 trigger; it changes no order (ruling 12). The row says what it is: "Alert policy flag (learned by
reward; synthetic, not validated)" (ruling 11). Its measured result (no gain beyond noise) stays in the card and on
the Analytics page.

### 5.1 The six numbers

From each policy file's `params` and `config` (content sha256 checked, see below). Ruling 1 decides which column
is the wired variant.

| | `policy.json` (with temperature, ledger incumbent) | `no_temperature/policy.json` |
|---|---|---|
| weight heart rate | 0.614 | 0.3372 |
| weight respiratory rate | 0.5247 | 0.5534 |
| weight SpO2 | 1.0638 | 1.2459 |
| weight temperature | 0.3149 | 0.0 |
| theta | 2.9776 | 3.3703 |
| p (persistence, bins) | 5 | 3 |
| `config.form` | `"sum"` | `"sum"` |
| chosen generation | 2 (round 1) | 1 |
| validation reward (seeds 341-400) | 0.992414 | 1.343958 |

Load rule (`reward_loop.load_policy`): parse the JSON; `body` = the object without `"sha256"`;
`sha256(json.dumps(body, sort_keys=True, separators=(",", ":")))` must equal the file's `"sha256"`; `temperature`
must be true for `policy.json` and false for `no_temperature/policy.json`; `Params.from_dict` checks four finite
non-negative weights, theta finite and at least 0.1, p an integer from 1 to 8 (no bools, no string numbers);
`digest(config) == config_sha256`; a no-temperature policy must have temperature weight 0.0. Refuse otherwise.
Both shipped files pass this check (recomputed for this hand-off).

### 5.2 z, score and state

- z per vital at scored bin t (`reward_policy.z_series`): `B_v = baseline_finding(v, series_v, DEFAULTS)` on the
  same series as section 4.2. If a tuple: `z_v = max(0.0, bad_v * B_v[1].z)`. This z is clipped at 0 in the bad
  direction. Research gives 0.0 while a baseline forms; the product does not compute the policy then (ruling 10).
- Bad direction (`baseline.VITALS[v].bad`): heart rate +1 (rising is bad), respiratory rate +1, SpO2 -1 (falling is
  bad), temperature +1.
- Score (form `"sum"`): with temperature `s = 0.614 * z_hr + 0.5247 * z_rr + 1.0638 * z_spo2 + 0.3149 * z_temp`;
  without, `s = 0.3372 * z_hr + 0.5534 * z_rr + 1.2459 * z_spo2` (weight 0.0 on temperature).
- Persistence: the run counts consecutive scored bins with `s >= theta` (2.9776 with temperature, 3.3703 without).
  A NOT RANKED bin ends the run and is not alerting. A missing bin (no row in that 15-minute slot) also ends the
  run. Alerting when `run >= p` (5 with temperature, 3 without).

State machine (`reward_policy.states`), per stay, bins in time order:

```
run = 0; previous = None
for each bin with a stored row (minute m, z or NOT RANKED):
    if previous is not None and m - previous != 15:   # a missing bin in between
        run = 0
    previous = m
    if bin is NOT RANKED:
        run = 0; state[m] = False; continue
    s = sum(w[v] * z[v] for v in (heart_rate, respiratory_rate, spo2, temperature))
    run = run + 1 if s >= theta else 0
    state[m] = run >= p
```

A gap resets the run but the bin after it is still scored: with `s >= theta` its run is 1. Because the state is
`run >= p`, the state at the current bin t depends only on bins t - p + 1 to t (t - 4 to t with temperature,
t - 2 to t without): each must be a stored, scored bin with a formed baseline and `s >= theta`. The product
computes it from those bins on every read and stores nothing (ruling 5). An earlier bin's scored status is rebuilt
on read by running `score_news2` on that bin's stored inputs; scores stay unstored. Where it cannot be rebuilt, the
policy is not computed for that bin (ruling 6). The dashboard session checks first whether earlier bins' NEWS2
inputs can be rebuilt at all; if they cannot, the policy is NOT shipped as "always not computed": the question goes
back to the orchestrator first.

### 5.3 What `PolicySignal` receives

| Case | `PolicySignal.alert` | Flag on the row |
|---|---|---|
| NOT RANKED bin (ruling 2) | `NotComputed("not ranked")` | none |
| Baseline forming (ruling 10) | `NotComputed("baseline forming")` | none |
| An earlier bin's scored status cannot be rebuilt from stored inputs (ruling 6) | `NotComputed("earlier bin not stored")` | none |
| Stay past 672 bins, or a vital not `counted()` (ruling 7) | `NotComputed(<reason>)` | none |
| Policy file missing or refused (ruling 13) | `NotComputed("policy not loaded")` | none |
| Computed, not alerting | `Computed(False)` | none |
| Computed, alerting | `Computed(True)` | "Alert policy flag (learned by reward; synthetic, not validated)" |

### 5.4 The variant, and what the card shows

Ruled (ruling 1): the wired variant is set by what the backend stores (temperature history or not), never by its
held-out row. The table below is for reading the card, not for choosing. What the card
(`models/tier_b/reward_policy/card.md`, from `held_out.txt`, ward-v2 seeds 11-30, scored once) shows:

| Held-out | NEWS2 chart trigger | Final, with temperature | Final, no temperature |
|---|---|---|---|
| Alone: alerts per patient per 12 h (95% CI) | 0.414 (0.395-0.434) | 0.153 (0.140-0.165) | 0.131 (0.119-0.143) |
| Alone: time in alert | 0.158 | 0.035 | 0.021 |
| Alone: median lead (min) | 47.2 | -3.8 | 20.2 |
| NEWS2 OR policy: any-bin, all episodes (95% CI) | 0.626 (0.522-0.727) | 0.703 (0.608-0.798) | 0.692 (0.596-0.785) |
| NEWS2 OR policy: alerts per patient per 12 h | 0.414 | 0.463 | 0.451 |

- Added to NEWS2, neither policy gives a gain beyond noise: the intervals overlap. Both raise more alerts.
- With temperature, the policy alone alerts late (median lead -3.8 min). That is not early warning. Without
  temperature its median lead alone is 20.2 min. Neither fact chooses the variant (ruling 1).

## 6. The confidence check (`TierCSignal.check` and `Guarantees`)

### 6.1 The check

`research/confidence.py` `check(p, t, q)`; q None means +infinity:

| Condition | Result (`check` field) | Flag |
|---|---|---|
| `p < t` | `"quiet"` | no |
| `p >= t` and q is not None and `p > q` | `"alert"` | yes: "Prediction model flag (synthetic, not validated)" |
| `p >= t` and (q is None or `p <= q`) | `"not_sure"` | no, never; recorded |

Mathematically the check is a second threshold, `max(t, q)`, with a strict `>` against q. On a live run q is still
applied as the stricter bar (ruling 4).

### 6.2 q

| Variant | File | q | t | alpha | n, j, k | Binds ONNX sha256 | Status |
|---|---|---|---|---|---|---|---|
| `all` | `models/tier_c/confidence_check/calibration.json` | 0.019854247570037842 | 0.006 | `"0.01"` | 3238, 32, 3207 | `d1ae4e881732922024b08c6cf9c37adbd8b01c48bc8390ee22b28ab802ddfdbc` | wired with the `all` student (ruling 1) |
| `no_temp` | `models/tier_c/confidence_check/calibration_no_temp.json` | 0.018539994955062866 | 0.006 | `"0.01"` | 3238, 32, 3207 | `e6b6ae44c11aa38fc38a961eebaf3e3bd51b1b5812b9e200d31d8c04101869ee` | wired with the `no_temp` student (ruling 1) |

Both are binding (q >= t; code: `Calibration.binding` is `q is None or q >= t`). Calibration data: ward-v2-ext
seeds 301-340, one negative bin per non-starting stay, draw seed 20261113.

### 6.3 The file binds one model; refuse it on any difference

Port `confidence.load` and `confidence.verify`. Refuse the file when any of these fails; then `check` is
`NotComputed("calibration not loaded")` on every bin and the student never flags (ruling 14):

- The file is at most 64 KiB and its sha256 equals the text of the sibling `<name>.sha256` (stripped).
- The field set is exactly `alpha, days, draw_seed, j, k, n, negative_bins, onnx_sha256, q, scenario_sha256, seeds,
  speed, t, variant`, with the code's types.
- `alpha == "0.01"` (a string) and `draw_seed == 20261113`.
- `(j, k)` equals `indices(n)` (`j = floor(alpha * (n + 1))` in exact fractions, `k = n + 1 - j`), and q is null
  exactly when j is 0.
- `variant` is the wired student's variant; `onnx_sha256` equals that student file's `source.onnx_sha256`; `t`
  equals the student's threshold.

The file also records the simulator setting it was calibrated on (days 30, speed 60, scenario sha256
`95052d021b73da3fa71cc62601a795264b8ffd1cf5c4bf2e6e3ba407f74698a8`). Research refuses to claim the target in any
other setting. A live run is another setting, so ruling 4 applies: the file is still used for q, and the
certificate is False.

Retraining or re-exporting the student changes the ONNX sha256 and so invalidates q. A new q needs a new
calibration run (`python -m research.confidence calibrate`). Never copy q to another model.

### 6.4 What `Guarantees` receives

| Field | Value |
|---|---|
| `conformal_certificate_current` | `False` on every live run (ruling 4), whether or not the calibration file loaded |
| `drift_flagged` | `None`. Nothing shipped computes drift |

The per-bin result is in `TierCSignal.check` (ruling 3), not here. Nothing on a row or a page claims coverage on a
live ward (ruling 4).

### 6.5 What `not_sure` does in the product

- It shows no flag and adds no reason. It is recorded as `TierCSignal.check = Computed("not_sure")`.
- It never raises an alert, never raises a patient's rank, never moves them up the list.
- The absence of a flag is never shown as "safe", "normal" or "low risk".
- It never suppresses a NEWS2 escalation. The check gates only the student's own flag. NEWS2's `escalated`, its
  reasons and its rank are computed without it.
- `quiet` means only that the student rule did not fire. No confident-quiet claim is made.

### 6.6 What the target means, and what must never be claimed

- In plain words, inside the simulator only: for a patient who is not starting to deteriorate, at most 1 in 100 of
  their 15-minute checks shows a supported alert, on average over stays.
- It is not precision. Positives are about 0.3% of bins (0.3143% on validation), so most supported alerts are
  still false.
- It is not per patient and not per shift. It must never be claimed as a clinical guarantee, and never claimed on a
  live ward (ruling 4): exchangeability holds only inside the simulator.
- The word "guarantee" never appears on a row or a page (ruling 11). The evaluation exporter already bans
  "conformal guarantee", and any positive "validated".

## 7. How the three meet in the arbiter

### 7.1 The arbiter today, and what the wiring adds

As the code is (`arbitrate`):

1. Rule 5 first: if NEWS2 abstains (`News2Abstained`), return `NotRanked` naming the missing inputs. Nothing else
   is looked at.
2. Rule 1: `escalated = tier_a.risk in {low_medium, medium, high}` (a single parameter at 3, a total of 5-6, a total
   of 7 or more). Reasons from NEWS2 only.
3. Rule 3: each significant `TierBSignal` finding adds a reason (`EscalationRule.tier_b_finding`) and sets
   `flagged`. `flagged` orders only patients with no trigger and an equal total; it never sets `escalated`.
4. Rule 2 (`TierCSignal`), the learned policy (`PolicySignal`), rule 4 and `Guarantees`: inert. Supplying one (any
   non-None value, `NotComputed` included) only adds its name (`rule_2_tier_c`, `learned_policy`, `guarantees`) to
   `inert`.
5. Ward order (`Ranked.sort_key`): trigger level, then NEWS2 total, then the Tier B flag (below a trigger only), then
   patient id.

What the wiring adds (rulings 3, 11, 12), and nothing more:

- On a `Ranked` decision, `TierCSignal.check == Computed("alert")` appends one `ArbiterReason` with the text
  "Prediction model flag (synthetic, not validated)"; `PolicySignal.alert == Computed(True)` appends one with the
  text "Alert policy flag (learned by reward; synthetic, not validated)".
- Neither sets `escalated` or `flagged`, and neither enters `sort_key`. The order is unchanged.
- The `rule` value of those reasons: `EscalationRule` (`schemas/tiers.py`) today has `tier_a_threshold`,
  `tier_c_raise`, `tier_b_finding` and `missing_input`. The wiring slice adds two new members, one for each flag
  (names its own); `tier_c_raise` is not reused. It corrects the arbiter's module docstring, which still says
  "Tier C may raise", to say a flag adds a reason and changes no order. The new reasons never set `flagged`:
  `flagged` keeps reading only `tier_b_finding` reasons, so they never move a row.
- A `NotRanked` decision carries no reasons, so it never shows a model flag.

### 7.2 Decision table (ruled behaviour)

Every row: order unchanged, and NEWS2's `escalated` and reasons exactly as NEWS2 decides.

| NEWS2 result | Own-baseline finding | Student p and check | Policy state | `TierCSignal` (`probability`, `check`) | `PolicySignal.alert` | `Guarantees.conformal_certificate_current` | Flags shown on the row | Order |
|---|---|---|---|---|---|---|---|---|
| NOT RANKED | not computed | none | none | `NotComputed("not ranked")`, `NotComputed("not ranked")` | `NotComputed("not ranked")` | False | none (`NotRanked` row) | unchanged |
| scored | baseline forming | none | none | `NotComputed("baseline forming")`, same | `NotComputed("baseline forming")` | False | none | unchanged |
| scored | any | student file failed its checksum | any | `NotComputed("student not loaded")`, same | as computed | False | policy flag only, if alerting | unchanged |
| scored | any | calibration failed its checksum | any | `Computed(p)`, `NotComputed("calibration not loaded")` | as computed | False | policy flag only, if alerting | unchanged |
| scored | any | any | policy file failed its checksum | as computed | `NotComputed("policy not loaded")` | False | prediction flag only, if `alert` | unchanged |
| scored | formed | p < t, `quiet` | not alerting | `Computed(p)`, `Computed("quiet")` | `Computed(False)` | False | none | unchanged |
| scored | formed | t <= p <= q, `not_sure` | not alerting | `Computed(p)`, `Computed("not_sure")` (recorded) | `Computed(False)` | False | none | unchanged |
| scored | formed | p > q, `alert` | not alerting | `Computed(p)`, `Computed("alert")` | `Computed(False)` | False | "Prediction model flag (synthetic, not validated)" | unchanged |
| scored | formed | p < t or `not_sure` | alerting | `Computed(p)`, `Computed("quiet")` or `Computed("not_sure")` | `Computed(True)` | False | "Alert policy flag (learned by reward; synthetic, not validated)" | unchanged |
| scored | formed | p > q, `alert` | alerting | `Computed(p)`, `Computed("alert")` | `Computed(True)` | False | both flags | unchanged |
| scored | formed | any | earlier bin's scored status cannot be rebuilt | as computed | `NotComputed("earlier bin not stored")` | False | prediction flag only, if `alert` | unchanged |
| scored | stay past 672 bins | none | none | `NotComputed("stay past 672 bins")`, same | `NotComputed("stay past 672 bins")` | False | none | unchanged |

The own-baseline finding (`TierBSignal`) keeps its own reason and its tie-break exactly as today. The invariant the
script promises (ruling 12): no single model can push a patient up the list. A flag adds a reason and changes no
order, for both models.

## 8. Flags, the current bin, series construction, provenance and logging

### 8.1 A flag is the current bin's state

- A flag is the state of the current bin (section 3), recomputed on every read; nothing is stored, and `decide`
  stays pure (ruling 5).
- "Current" is the last completed stored 15-minute bin, with that bin's stored inputs (ruling 6). NEWS2 is scored on
  the live vitals, so a flag can lag NEWS2 by up to 15 minutes.
- The product opens no alerts and keeps no alert state for these models. The student's flag is
  `check == "alert"` on the current bin; the policy's flag is its state on the current bin.

### 8.2 Abstention, and what must never happen

- Not computed, with no flag: NOT RANKED bin; baseline forming; a stay past 672 bins; a vital not `counted()`; an
  earlier bin's scored status that cannot be rebuilt from stored inputs (policy); a model, calibration or policy file missing or refused.
- What must never happen: imputing or carrying forward a value; computing on a NOT RANKED bin; reading a later bin;
  loading files of two variants, choosing a variant by a held-out row, or switching files per row; using q with any other ONNX; letting `not_sure` flag;
  letting a flag change NEWS2's score, risk, reasons, `escalated` or the order; a default p, t or q.

### 8.3 The evaluation alert rule (not used by the product)

The 16-bin re-arm rule is an evaluation convention only (ruling 5). It is how every card counted alerts, and how
t was chosen (section 4.7). It is given here so the cards can be read:

```
quiet = 16                      # starts armed
previous = None
for each bin with a stored row, in time order (minute m):
    if previous is not None:
        quiet += (m - previous) // 15 - 1      # missing bins count as not alerting
    previous = m
    if alerting[m]:
        record m as alerting
        if quiet >= 16: open a new alert at m
        quiet = 0
    else:
        quiet += 1
```

One open alert per patient; a new alert opens only after 16 bins (4 hours) not alerting; a NOT RANKED bin and a
missing bin are not alerting (`alerts.scan`, `REARM_BINS = 16`).

### 8.4 Series construction (ruling 7) and provenance (ruling 8)

The features are built from the backend's own series: `services/scoring.py` `grid(points, admitted_at, vital)`
(a 15-minute grid from the first stored bin at or after admission, at most `MAX_BINS = 672` slots; a slot with no
row, a null, a non-finite or a physically impossible value is a gap, `possible()`) and `counted(state, vital)`
(only while the patch is sampling within `STALE_AFTER = 2 * BIN`, and the live reading is present and from the
device). Past 672 bins nothing is computed.

(a) REQUIRED parity test for the wiring slice (closes gap G3). Replay one simulator trial through the backend path
(ingest its bins as the simulator does, then build the features from `grid()` as the product will) and show that,
for a stay inside the cap with device values, the backend's inputs for the wired variant equal the research
inputs from `research.student.stay_features(rows)` then `research.student.view(x.astype(numpy.float32), temp,
"trend")` on the same stay (temp True for `all`, False for `no_temp`), for every bin the product computes. Proposed tolerance, which the wiring slice confirms: exact equality
after float32 rounding for values, flags, d1h and d4h; an absolute tolerance of 1e-6 for each z. Use a
ward-v2-ext training seed (61-160); never a held-out seed (ward-v2 11-30) or a calibration seed (301-340).

(b) Cases where backend and research features differ BY DESIGN. Each is not computed, never approximated:

| Case | Why they differ | Product result |
|---|---|---|
| A stay past 672 bins | `grid()` keeps the newest 672 slots, so the baseline anchor would move; research uses the whole stay | not computed |
| A physically impossible value | `possible()` makes it a gap in `grid()`; research keeps the stored value | not computed for that bin if the value is in the bins the features read |
| A stale patch or a nurse-typed live value | `counted()` is False; research has no such rule | not computed |
| An earlier bin whose scored status cannot be rebuilt from stored inputs | research has every row's `ranked`; the product rebuilds it on read (ruling 6) | the policy is not computed |
| The baseline still forming, for any vital in `HISTORY_VITALS` | research scored these bins with their z-missing flags set | not computed (ruling 10) |

Provenance (ruling 8). Each `Computed` carries, in a structured form: the model id (`tier_c/student_vitals`,
`tier_c/confidence_check`, `tier_b/reward_policy`), the wired variant (ruling 1), the file sha256 from the
manifest (section 2.2), the run id from the manifest (`m1-20261113-3d634f3`, `m3-20261113-fbd39f2`, `m2-20261113`,
the same for both variants), and the
current bin's start and end (end = start + 15 minutes). Today's `Provenance` has `source`, `tier`, `observed_at`,
`computed_at`, `model_id`, `synthetic` and `quality`, and `Window` has `start_s`, `end_s` and `coverage`: the bin's
start and end fit `observed_at` and the `Window`, and the model id fits `model_id`, but the variant, file sha256 and
run id have no field. The wiring slice adds structured fields to `Provenance` for them (names its own); it never
packs them into the `model_id` string.

### 8.5 Logging (ruling 15)

- A failure found at score time is logged once per reason per process, with a count: the first occurrence writes
  one line with the fixed reason; later occurrences only add to a per-reason counter in process memory, and the
  count is reported with that reason (how and when it is reported is the wiring slice's choice).
- A load failure at start is one warning line (ruling 13).
- No log line carries patient data or a feature value.

## 9. Files

Bytes from `stat`, sha256 from `shasum -a 256`, re-read on 2026-11-13; every model file matches `models/manifest.json`
as it is in this worktree. The API image holds the wired variant's three rows (ruling 1) plus the manifest
(section 2.1); the manifest stays the source if a file changes.

| Path | Bytes | sha256 | Role |
|---|---|---|---|
| `models/tier_c/student_vitals/model.json` | 34,949 | `d05d75ee0ad8da0db138a74f3a9796c98de6b8af5dfea513789d5521f1768af6` | student `all`, plain form; wired with temperature (ruling 1) |
| `models/tier_c/confidence_check/calibration.json` | 660 | `7815bd3885c794fafa38e8384d6837300c163738dbb6a0a7a126c4dca2484f11` | q for `all`; wired with `model.json` |
| `models/tier_c/confidence_check/calibration.json.sha256` | 65 | `e4017fd23c40974d97d7807a377c37672735c9334c7d94dbece771ecccdef0cf` | holds `calibration.json`'s sha256; wired with it |
| `models/tier_b/reward_policy/policy.json` | 5,064 | `21cf038e4ab699906eeab87d56ddbd39965915a8ec085e75f56ca99501114775` | policy with temperature (content sha256 `b364a680...f804f`); wired with temperature (ruling 1) |
| `models/manifest.json` | 6,706 | `20b3b81aeb9d327f49bb6f5edfbc1749757ecd01524f4aca5d26ee1aec1dfe76` (snapshot; it changes when regenerated) | the checksums; always in the image |
| `models/tier_c/student_vitals/model.onnx` | 5,334 | `d1ae4e881732922024b08c6cf9c37adbd8b01c48bc8390ee22b28ab802ddfdbc` | never loaded: its sha256 is read from `model.json` |
| `models/tier_c/student_vitals/metadata.json` | 28,290 | `4ef6898ca6e9e2ca81b90377586642a3556ff7343bdd1c8f66dbad324ad25094` | record only, not loaded |
| `models/tier_c/student_vitals/model_no_temp.json` | 14,903 | `d041b6584edc7d67c54dcdb03e81f0c1a58f51282a3cf02fbc3e869ff53cb2fc` | student `no_temp`, plain form; wired without temperature (ruling 1) |
| `models/tier_c/student_vitals/model_no_temp.onnx` | 2,382 | `e6b6ae44c11aa38fc38a961eebaf3e3bd51b1b5812b9e200d31d8c04101869ee` | never loaded: its sha256 is read from `model_no_temp.json` |
| `models/tier_c/confidence_check/calibration_no_temp.json` | 664 | `4a3de9544cce7e6cc838837bb2c1646261d85d1f41bef35f84135509fa0729bb` | q for `no_temp`; wired with `model_no_temp.json` |
| `models/tier_c/confidence_check/calibration_no_temp.json.sha256` | 65 | `484f8b1440da0715646ce7473e1f5ee3df010634ea2f0f15b2b9f436df053c55` | holds `calibration_no_temp.json`'s sha256; wired with it |
| `models/tier_b/reward_policy/no_temperature/policy.json` | 4,916 | `522195aa8104271d3bfec0e87ab1f37e07b074dccbd5a347d05191905a12a3b3` | policy without temperature (content sha256 `ef60e564...e020`); wired without temperature (ruling 1) |

Reference implementations to port or copy:

| Code | What to take | Dependencies |
|---|---|---|
| `research/plain_student.py` | `parse`, `load`, `forward` (copy verbatim into the backend, or port; never import `research/`, section 2.4) | standard library |
| `research/student.py` | `patient_features`, `stay_features`, `_names`, `view` (port the arithmetic, do not import) | numpy, MindSpore at import: never import it in the backend |
| `research/reward_policy.py` | `z_series`, `states`, `Params` | standard library and the backend's `baseline_finding` |
| `research/reward_loop.py` | `load_policy`, `digest` | standard library |
| `research/confidence.py` | `indices`, `check`, `Calibration`, `load`, `verify` | standard library |

No new backend dependency is needed: the plain JSON forward pass, the policy and the check use only the standard
library (`struct` for the float32 step included), and the baseline is already the backend's own
`baseline_finding`. Everything runs in the API process (section 2); there is no model service.

## 10. Test plan for the wiring slice

1. Forward parity: the backend port against `research/plain_student.py` on the fixed batch of section 4.8, for
   both student files, including the float32 step (ruling 9): the rounded batch gives the expected p within 1e-12, and the unrounded
   batch through the backend's own rounding gives the same p.
2. Trial replay parity (ruling 7, REQUIRED): section 8.4 (a), with its stated tolerance.
3. Feature fixture on short stays: `research.trials.load_trial("research/tests/data/toy")`, rows grouped by
   patient, `research.student.stay_features(rows)` and `view(x.astype(numpy.float32), temp, "trend")` for both
   values of temp, plus one
   long stay built like `research/tests/test_reward.py` `stay()` (94 bins) with one gap and one NOT RANKED bin after
   bin 24. The toy stays are at most 14 bins, so every toy bin must be `NotComputed("baseline forming")` in the
   backend (ruling 10); on the long stay every bin from the formed baseline on must match the research row.
4. Policy parity: on the same stays, the backend's states against
   `research.reward_policy.states(Params.from_dict(policy["params"]), research.reward_policy.z_series(rows), "sum")`
   on every bin the backend computes, for both policy files; and `NotComputed("earlier bin not stored")` when the
   scored status of one of bins t - p + 1 to t - 1 cannot be rebuilt from its stored inputs (ruling 6).
5. Check truth table: p below t; p equal to t; p between t and q; p equal to q (`not_sure`); p above q; q None
   (never `alert`).
6. `not_sure` shows no flag and is recorded: for every bin with `t <= p <= q`, the row has no prediction-model
   reason and `TierCSignal.check == Computed("not_sure")`.
7. Failed calibration gives no flag: with the calibration refused, `check` is `NotComputed(...)` on every bin and no
   row shows the prediction-model reason, while `probability` is still `Computed(p)` (ruling 14).
8. File refusals at start (ruling 13): each wired file missing, unreadable, over its cap, with a changed byte
   (manifest sha256 differs), with no manifest record; a wrong `format`; a calibration `.sha256` that differs; a
   calibration with another `onnx_sha256`; a policy with a changed weight but the old `sha256`. Each gives
   `NotComputed` for its signal, exactly one warning line at start, no fall-back to the other variant, and NEWS2
   output unchanged. A build that puts files of both variants in the image is refused.
9. Order unchanged with flags on: for a ward fixture where both flags fire on some rows, the decisions with the
   hooks on and off have identical `news2`, `escalated`, `flagged`, NEWS2 and Tier B reasons and rank; the only
   differences are the appended flag reasons (with the exact ruling 11 texts) and `inert`.
10. Live certificate: `Guarantees.conformal_certificate_current` is False on every live run, with the calibration
    loaded or not (ruling 4), and no row or page text contains "guarantee" or a coverage claim.
11. NOT RANKED, forming and the cap: a NOT RANKED bin gives `NotComputed("not ranked")` on both hooks (ruling 2);
    a stay of 673 bins gives not computed (ruling 7); the current bin is the last completed stored bin (ruling 6).
12. Logging: one score-time failure reason repeated many times gives one log line and a count (ruling 15).
13. Baseline gate: with `HISTORY_VITALS` as it is, a stay where heart rate and respiratory rate have 24 valid bins but
    SpO2 has 23 gives `NotComputed("baseline forming")` on both hooks (ruling 10).

## 11. Limits and what was not checked

- Not run on a ward hub, a small ARM board or the team server. The pure-Python forward pass was timed on one
  machine (section 2.5); the feature computation, the policy and the check were not timed.
- Circular synthetic data: one simulator makes the episodes, the vitals and the judgement. Every number is an upper
  bound inside the simulator, not a performance claim.
- The research evaluation scored the forming bins; the product does not (ruling 10). The cards' numbers include
  those bins.
- The student never saw a non-alert consciousness level or a missing device vital in training (section 4.5).
- The simulator's bin-minute convention (start or end of the bin) was not checked here; it lives in the simulator
  repo.
- Validation and held-out facts are in the cards: `models/tier_c/student_vitals/card.md`,
  `models/tier_b/reward_policy/card.md`, `models/tier_c/confidence_check/card.md`.

## Rulings (orchestrator, 2026-11-13, by the owner's delegation M-2)

Owner decisions:

- H-1: when connected, the prediction model, the alert policy and the confidence check run inside the API process
  on the team server; no model service, no network call per score, no Huawei Cloud account for it, no new backend
  dependency.
- M-1: the alert policy IS connected, as a flag; it can add a reason to a ward row, never outranks or lowers a
  NEWS2 trigger, and the row says what it is (learned by reward, synthetic, not validated).
- M-2: the wiring decisions are delegated to the orchestrator inside fixed rules, and each choice is written down.

Fixed rules: a model may flag and never outranks or lowers a NEWS2 trigger; "not sure" shows as no flag and is
recorded; a missing or bad model file means not computed, never a default.

1. Open: which variant pair and which policy file to wire.
   Ruling: (a) The with-temperature files: student `model.json`, `calibration.json`, policy `policy.json`. Never
   pick a variant by its held-out row. When temperature is missing for a bin, the result is what the trained model
   does with its missing flag; do not switch files per row.
   Condition (follow-up answer 3): the with-temperature files are wired UNLESS the backend keeps no temperature
   history. The dashboard session checks whether temperature history exists in the backend at all; if it does not,
   the no-temperature files (`model_no_temp.json`, `calibration_no_temp.json`, `no_temperature/policy.json`) are
   wired, for that stated reason only (what the backend stores), never because of a held-out row.
   For the code: the image holds one variant's three files and the manifest (section 2.1). Today
   `HISTORY_VITALS = ("heart_rate", "respiratory_rate", "spo2")` carries no temperature, and temperature bins are
   read only for the 12-bin sparkline. A temperature gap in the current bin makes it NOT RANKED; a gap in a lag bin
   sets that lag's flag.
2. Open: what a NOT RANKED bin gives each hook.
   Ruling: (a) `NotComputed("not ranked")` on both hooks.
   For the code: `TierCSignal(probability=NotComputed("not ranked"), check=NotComputed("not ranked"))` and
   `PolicySignal(alert=NotComputed("not ranked"))`; `arbitrate` lists them in `NotRanked.inert`.
3. Open: where the per-bin check result is carried.
   Ruling: (a) One new field on `TierCSignal`: a `Result` of `alert` / `not_sure` / `quiet`. Only `alert` may flag;
   `not_sure` is recorded and shows no flag.
   For the code: add, for example, `check: Result[Literal["alert", "not_sure", "quiet"]]` to `TierCSignal` (name
   the wiring slice's); fill it from `confidence.check(p, t, q)`, with `"not sure"` spelt `"not_sure"`.
4. Open: `conformal_certificate_current` on a live run.
   Ruling: (a) `conformal_certificate_current` is False on every live run; q is still applied as the stricter bar.
   The page and the row never claim coverage on a live ward.
   For the code: `Guarantees(conformal_certificate_current=False, drift_flagged=None)`; `check` still uses q from
   `calibration.json`.
5. Open: whether the product raises alerts and stores alert state.
   Ruling: (a) A flag is the current bin's state, recomputed on every read, nothing stored. `decide` stays pure.
   For the code: no re-arm counter and no alert table; `alerts.scan` (16 bins) stays an evaluation convention
   (section 8.3).
6. Open: which bin is "current", and earlier bins' scored status.
   Ruling: (a) The last completed stored 15-minute bin with that bin's stored inputs. A flag can lag NEWS2 by up to
   15 minutes; say so in the document. Where an earlier bin's scored status is not stored, the policy is not
   computed.
   Follow-up answer 4: the dashboard session checks whether earlier bins' NEWS2 inputs can be rebuilt on read from
   stored inputs. Scores stay unstored. If they cannot be rebuilt, the policy is not shipped as "always not
   computed"; it comes back to the orchestrator first.
   For the code: `score_news2` on the current bin's stored inputs decides "scored" for the features; the policy
   rebuilds bins t - p + 1 to t - 1 the same way on read and gives `NotComputed(...)` when one cannot be rebuilt.
7. Open: how the series is built.
   Ruling: (a) The backend's `grid()` and `counted()`, and not computed past 672 bins. Because of gap G3, one added
   requirement for the wiring slice: a parity test that replays one simulator trial through the backend path and
   shows the features equal the research features for a stay inside the cap with device values (state the
   tolerance), and a list in the document of the cases where they differ by design (those cases are not computed,
   not approximated).
   For the code: features from `services/scoring.grid` and `counted`, `MAX_BINS = 672`; the parity test and the
   by-design list are in section 8.4.
8. Open: the `Provenance` and `Window` values.
   Ruling: The structured form: model id, variant, file sha256 from the manifest, run id, the bin's start and end.
   Follow-up answer 1: the wiring slice adds structured fields to `Provenance`.
   For the code: the bin's start and end in `observed_at` and the `Window`, the model id in `model_id`; new
   structured `Provenance` fields for the variant, the file sha256 and the run id (section 8.4).
9. Open: float32 rounding before the forward pass.
   Ruling: (a) float32 before the forward pass.
   For the code: `struct.unpack("f", struct.pack("f", x))[0]` on each feature, then `forward`; in the parity test.
10. Open: whether p and the policy state are computed in the first hours of a stay.
    Ruling: (b) Not computed until the patient's own baseline has formed (24 valid bins, 6 hours).
    Follow-up answer 3: "the baseline has formed" means formed for every vital the backend keeps history for
    (`HISTORY_VITALS`), gating both the student and the policy.
    For the code: while `baseline_finding` returns its "baseline forming" `NotComputed` for any vital in
    `HISTORY_VITALS` (today heart rate, respiratory rate, SpO2), `TierCSignal` and `PolicySignal` both get
    `NotComputed("baseline forming")`. Research scored these bins; the product only removes them.
11. Open: the row text.
    Ruling: Short row text, in the style of the baseline reason already on main: "Prediction model flag (synthetic,
    not validated)" and "Alert policy flag (learned by reward; synthetic, not validated)". `not_sure` shows no flag.
    The word "guarantee" never appears on a row or a page.
    Follow-up answer 2: two new `EscalationRule` members carry these reasons; `tier_c_raise` is not reused.
    For the code: those two strings exactly, as `ArbiterReason.text`, each with its own new `EscalationRule` member;
    no text for `not_sure`.
12. Open: whether a signal may change order or escalation.
    Ruling: (a) A flag adds a reason and changes no order, for both models. The script's own line is that no single
    model can push a patient up the list.
    Follow-up answer 2: the arbiter docstring is corrected; the new reasons never set `flagged`.
    For the code: append an `ArbiterReason`; never set `escalated` or `flagged`, never touch `Ranked.sort_key`;
    correct the "Tier C may raise" line of the `arbiter.py` module docstring.
13. Open: where the files live and how a changed file is handled.
    Ruling: (a) Copied into the API image at build, verified against `models/manifest.json` at start; a mismatch
    means not computed and one warning line.
    For the code: the build copies the wired variant's files (ruling 1) and the manifest; start-up runs the checks
    of section 2.2 once.
14. Open: how a failed calibration is shown.
    Ruling: (a) With a failed or missing calibration the student gives no flag, and the reason is carried in the
    field of ruling 3.
    For the code: `check = NotComputed("calibration not loaded")`, `probability` still `Computed(p)`, no reason
    appended.
15. Open: how a per-bin failure is logged.
    Ruling: Once per reason per process, with a count.
    For the code: section 8.5.

### The three gaps

G1. No hook could carry the per-bin check result (`alert`, `not_sure`, `quiet`): `TierCSignal` held only p and
`Guarantees` holds two per-patient bools. Closed by ruling 3: the new `TierCSignal` field.

G2. The calibration records one simulator setting, so research refuses to claim the target in any other, and a
live ward run is another setting. Closed by ruling 4: the certificate is False on every live run, q still acts as
the stricter bar, and nothing claims coverage on a live ward.

G3. The backend builds the series differently from research (`grid()` with its 672-bin cap and `possible()`, and
`counted()`). Closed by ruling 7's parity test (section 8.4 (a)) and its by-design list (section 8.4 (b)).

### Facts that stay in the cards and on the Analytics page

The policy added to NEWS2: 0.703 against 0.626 (any-bin, all episodes) for 12% more alerts (0.463 against 0.414 per
patient per 12 h), median lead alone -3.8 minutes (`models/tier_b/reward_policy/card.md`). The student added to
NEWS2: 0.747 against 0.626 at 0.525 against 0.414 alerts (`models/tier_c/student_vitals/held_out.json`). No gain
beyond noise for either: the intervals overlap.
