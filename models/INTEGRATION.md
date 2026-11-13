# Wiring the three research models into the arbiter: a hand-off

This file says how to RUN the shipped files. It wires nothing. The wiring slice can follow it without asking
the models session anything. Where this file and the code disagree, the code is right; the files named here are
the sources of truth.

## 1. Scope and status

Three offline research models:

| Model | Folder | What it gives per scored bin | Arbiter hook |
|---|---|---|---|
| Distilled vitals student | `models/tier_c/student_vitals/` | a probability p | `TierCSignal(probability: Result[float])` |
| Reward-driven alerting policy | `models/tier_b/reward_policy/` | alerting or not (a bool) | `PolicySignal(alert: Result[bool])` |
| Confidence check on the student | `models/tier_c/confidence_check/` | "alert", "not sure" or "quiet", from p | `Guarantees` (see section 5) |

- All three are research only. They were trained and judged on synthetic simulator trials. They are not
  validated. No claim about real patients is made.
- Held-out results are in the cards. The reward policy's held-out numbers exist (section 4). The student's and
  the check's held-out runs are being produced now; read them from the cards, not from this file.
- Nothing here changes NEWS2. NEWS2 is never learned, re-weighted or overridden. NEWS2 stays the only thing that
  sets `escalated`.
- Today every one of these hooks is inert in `backend/src/biosentry/domain/arbiter.py`: the arbiter accepts it,
  records its name in `Ranked.inert` or `NotRanked.inert`, and changes nothing.

## 2. The bin, the stay and the history the features need

- Bin: one 15-minute bin per patient (`research/trials.py` `BIN_MINUTES = 15`; backend
  `services/scoring.py` `BIN = timedelta(minutes=15)`). Each vital has at most one stored value per bin (the
  simulator's 15-minute median for heart rate, respiratory rate and SpO2). A bin with no row, or a null value, is
  a gap. Nothing is filled.
- Minute convention in the research code: a trial row carries `minute`, a multiple of 15, with
  `0 < minute < days * 1440`. A stay's bins run from its first row to its last row, every 15 minutes
  (`range(min(rows), max(rows) + 1, BIN_MINUTES)`). The code only ever uses differences of bins, never clock
  time. Whether the simulator's minute labels a bin's start or its end is defined in the simulator repo
  (`docs/trial-format.md`), not here. The backend stores a bin at its start time (`schemas/baseline.py`: the bin's
  end is `latest_at + 15 min`). The wiring only needs one rule, used everywhere: bin i is the i-th 15-minute slot
  of the stay.
- Scored bin: a bin where the arbiter would rank the patient, that is `score_news2(...)` returns a `News2Score`
  (all eight NEWS2 inputs present and possible) and `arbitrate(...)` returns `Ranked`. A NOT RANKED bin is one
  where NEWS2 returns `News2Abstained`. In the research data `Row.ranked` is this flag, and `research/rescore.py`
  `check_parity` refuses a trial where it differs from the backend's own `score_news2`.
- Consequence: on a scored bin all four device vitals (heart rate, respiratory rate, SpO2, temperature) are
  present, because NEWS2 needs them. A scored bin never has a missing device vital. The value-missing flags are
  therefore always 0 on scored bins (and were constant 0 in training, section 3.4).
- Stay: one patient's admission, from the first bin to the last. All history below is of the same stay only.
- History the features need, with the constants from the code:

| Need | Constant | Source |
|---|---|---|
| Own baseline window | the first 24 valid bins of the stay, then frozen | `baseline.py` `DEFAULTS = BaselineParams()`, `window_bins = 24` |
| "Forming" period | z is not computed until the stay has 24 valid values of that vital, counting the current bin | `baseline_finding` returns `NotComputed("baseline forming, n of 24 bins")` |
| 1-hour lag | 4 bins earlier | `student.py` `LAG_1H = 4` |
| 4-hour lag | 16 bins earlier | `student.py` `LAG_4H = 16` |
| Policy persistence | the last p scored bins in a row (p = 5 or 3) | `policy.json` `params.p` |
| Alert re-arm | 16 bins not alerting | `alerts.py` `REARM_BINS = 16` |

- A valid bin for the baseline is a value that is not None, not a bool and finite (`baseline._valid`). Values of
  NOT RANKED bins count: they feed the baseline and the lags even though the bin itself is not scored.
- So the feature code needs the whole stay's series for each device vital (the baseline anchor is the first 24
  valid bins since admission), plus the current bin's NEWS2 inputs. To also run the alert rule (section 7) it
  needs the states of the 16 earlier bins, which for the student means features back to bin t - 32, and for the
  policy z values back to bin t - 16 - (p - 1).

## 3. The student (`TierCSignal`)

### 3.1 Variants

| Variant | Plain file | ONNX file (sha256 binding) | Inputs | Hidden width | Parameters | Threshold t |
|---|---|---|---|---|---|---|
| `all` (with temperature) | `model.json` | `model.onnx` | 35 | 32 | 1,185 | 0.006 |
| `no_temp` | `model_no_temp.json` | `model_no_temp.onnx` | 27 | 16 | 465 | 0.006 |

Both shipped students are `inputs: "trend"` (base plus trend), distilled, loss MSE on the teacher's logits
(`metadata.json` `variants.*.student`). The input names below were printed from `metadata.json`
`features_student`, from `model.json` `inputs`, and from `research.student._names("trend", temp)`; all three are
identical, in this order.

### 3.2 Notation

For one stay and one vital v, `x_v[i]` is the stored value of v in bin i (i = 0 is the stay's first bin), or None
for a gap. t is the current, scored bin. `series_v = [x_v[0], ..., x_v[t]]`, with None in every gap and with the
values of NOT RANKED bins included. `B_v = baseline_finding(v, series_v, DEFAULTS)` (backend
`domain/scoring/tier_b/baseline.py`). `S = score_news2(inputs at bin t)`, a `News2Score` because t is scored.

### 3.3 The input vector, variant `all` (35 inputs)

| i | Name | Formula at scored bin t | Units | Missing rule | Bins read |
|---|---|---|---|---|---|
| 0 | `heart_rate` | `x_hr[t]` as stored (not rounded) | /min | None gives 0.0 (cannot happen on a scored bin) | t |
| 1 | `heart_rate_missing` | 1.0 if `x_hr[t]` is None, else 0.0 | flag | always 0.0 on a scored bin | t |
| 2 | `heart_rate_z` | `B_hr[1].z` when `B_hr` is a tuple, else 0.0 | sd | 0.0 while forming | 0..t |
| 3 | `heart_rate_z_missing` | 0.0 when `B_hr` is a tuple, else 1.0 | flag | 1.0 while forming | 0..t |
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

### 3.4 The input vector, variant `no_temp` (27 inputs)

The same definitions without any temperature input, and with one changed NEWS2 input:

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
- A bin is still scored only when NEWS2 has a temperature. "No temperature" means temperature does not feed the
  model; it does not remove the need for a temperature reading before a bin is scored.

### 3.5 Exact definitions of each family (from `research/student.py`)

- Value: `float(x_v[t])`, the stored bin value, not rounded. NEWS2 rounds for banding; the features do not.
- Value-missing flag: 1.0 when the value is None. On a scored bin this cannot happen. In training these columns
  were constant 0.0, so `model.json` stores mean 0.0 and std 1.0 for them, and their first-layer weights are about
  1e-30: they have no effect on p.
- z: `patient_features` calls `baseline_finding(v, series_v, DEFAULTS)` with the series up to and including t
  (gaps None, NOT RANKED values included). On a tuple it takes `Deviation.z = (x_v[t] - median) / spread`, where
  median is the median of the first 24 valid bins, `spread = max(1.4826 * MAD, floor_v)`, floors heart rate 2.0,
  respiratory rate 1.0, SpO2 1.0, temperature 0.1. This z is SIGNED and not clipped (the policy's z in section 4 is
  clipped; do not reuse one for the other). On `NotComputed` (fewer than 24 valid values: "forming"; or the latest
  bin missing, which cannot happen on a scored bin) the value is 0.0 and the z-missing flag is 1.0.
- NEWS2 total: `S.total` from the backend scorer on the bin's inputs (`research/rescore.score_row` builds them as
  the backend does).
- On oxygen: `float(supplemental_o2)`.
- Consciousness not A: `float(consciousness != "A")`. In training this column was constant 0.0 (no training bin
  had ACVPU other than A): mean 0.0, std 1.0, weights about 1e-30. The student therefore ignores this input; a
  patient who is not alert moves p only through the NEWS2 total.
- d1h and d4h (`stay_features`): `cur - lag`, with the lag bin 4 or 16 bins earlier on the stay's 15-minute grid.
  The lag bin is ANY stored bin of the same stay with a value, scored or NOT RANKED. A lag bin that has no row,
  has a null value, or falls before the stay's first bin is missing: the value is 0.0 and the flag 1.0. The
  current value is always present on a scored bin. The NEWS2 status of the lag bin is never looked at.
- Known artefact: the z-missing flags (first 24 valid bins) and the d1h and d4h flags (first 4 and 16 bins) also
  mark the first hours of a stay.
- Never an input: time since admission, patient id, anything about the episode, the outcome.

### 3.6 Standardisation and the forward pass

From `research/plain_student.py` (`load`, `parse`, `forward`), format `"biosentry-plain-mlp-1"`:

1. Check the row has exactly n values (35 or 27), each an `int` or `float` and finite. A bool is refused
   (`type(v) not in (int, float)`), so pass flags as 0.0 and 1.0, never True and False.
2. `h0[i] = (x[i] - mean[i]) / std[i]`, then clamp to [-1e6, 1e6] (`CLAMP`).
3. Layer 0, for each hidden unit u: `acc = 0.0; for i: acc += h0[i] * W0[u][i]`; then `a[u] = max(acc + b0[u], 0.0)`
   (ReLU).
4. Layer 1: `acc = 0.0; for u: acc += a[u] * W1[0][u]`; `z = acc + b1[0]`.
5. `p = sigmoid(z)`, computed as `1 / (1 + exp(-z))` for z >= 0 and `exp(z) / (1 + exp(z))` for z < 0, then
   clamped to [0, 1].

Orientation: `weights[unit][input]`. The outer index is the output unit and the inner index is the input. This is
the transpose of the ONNX initialiser (`[input][unit]`). Layer 0 is 32 x 35 (`all`) or 16 x 27 (`no_temp`); layer 1
is 1 x 32 or 1 x 16. `mean` and `std` are the training rows' column moments (seeds 61-160); a zero std became 1.0.
Every number is a float32 value written with Python's `repr`. The arithmetic is double precision in the ONNX
graph's order (Sub, Div, Gemm, Relu, Gemm, Sigmoid).

Float32 inputs: the research scorer casts the feature matrix to float32 before the model
(`Scorer.__call__`: `view(x.astype(np.float32), ...)`), and the ONNX graph takes float32. To reproduce the research
numbers exactly, compute each feature in double precision, then round it to float32 before the forward pass
(standard library: `struct.unpack("f", struct.pack("f", x))[0]`). See "Decisions".

Reader rules to keep in a port (`parse`): exact key set; `format`, `hidden_activation == "relu"`,
`output == "sigmoid"`; exactly two layers; layer 1 has one unit; every std above 0; distinct input names; no NaN or
Infinity; file at most 1 MiB; threshold null or in [0, 1].

### 3.7 The output and the label

- p is the student's probability that this scored bin lies in the onset window of a deterioration episode.
- Label (`patient_labels`): 1 from `episode_start` to fully in (`episode_start + ramp_minutes`), both ends
  included, on the bin's minute; bins after fully in are excluded from training (-1); patients present at the
  trial start (admit minute 0) are excluded (-2); 0 otherwise. `outcome` is never a label.
- Threshold t = 0.006 (both variants; `model.json` `threshold`, `metadata.json`
  `variants.*.thresholds.student.threshold`). It is the smallest t in the grid 0.001 to 0.009 (step 0.001), then
  0.01 to 0.99 (step 0.01) whose alerts AND time in alert on validation seeds 261-300, under the alert rule of
  section 7 with "alerting when p >= t", are both at most the NEWS2 chart trigger's. It was computed from the ONNX
  output.
- The student's own alert state on bin t is `p >= t` (`student.prob_policy`). With the confidence check it is
  `check(p, t, q) == "alert"` (section 5).

### 3.8 Numerical tolerance and the port's parity test

- Research tolerance (README "Plain export", `tests_ml/test_export_plain.py`): on a fixed batch of 1,000 rows the
  plain forward pass is within 1e-6 of onnxruntime and within 1e-5 of MindSpore and MindIR. That test runs in the
  research ML gate, not in the backend.
- Parity test for the backend port, with no numpy:
  1. Once, in the research environment (standard library only), build a fixed batch per variant from
     `model.json`: `rng = random.Random(7)`; rows 0-799: `x[i] = mean[i] + rng.uniform(-4, 4) * std[i]`; rows
     800-999: the same, then each value replaced, with probability 0.5, by 0.0 or 1.0 at random (this mirrors the
     research batch, which also mixes flags). Round every value to float32.
  2. Expected output: `research.plain_student.forward(research.plain_student.load(path), row)` per row.
  3. Commit `{"model_sha256": <sha256 of model.json>, "rows": [...], "p": [...]}` as a backend test fixture.
  4. The backend test loads the committed `model.json` through the port, asserts its sha256 equals the fixture's,
     and asserts `abs(p_port - p_expected) <= 1e-12` for every row. A verbatim copy of `forward` gives exact
     equality; keep the tolerance only for a port that reorders nothing but differs in the last bit.
  5. Also assert the threshold read from the file is exactly 0.006, and that a row of the wrong length, a NaN, an
     infinity and a bool are each refused.

### 3.9 What `TierCSignal` receives

| Case | `TierCSignal.probability` |
|---|---|
| NOT RANKED bin (NEWS2 abstains) | `NotComputed("not ranked")`, or no `TierCSignal` at all (see "Decisions"). Never a p: research computes no row for a NOT RANKED bin |
| Scored bin, model file loaded and checked | `Computed(p)` |
| Scored bin, baseline forming (fewer than 24 valid values of a vital) | `Computed(p)`. The student does not abstain: z is 0.0 and the z-missing flag 1.0 |
| Scored bin, fewer than 4 or 16 earlier bins, or a lag bin missing | `Computed(p)`, with the d1h or d4h value 0.0 and its flag 1.0 |
| Scored bin, a device vital missing | cannot happen: NEWS2 abstains first, so the bin is NOT RANKED |
| Model file missing, refused by the reader, or its sha256 differs from section 8 | `NotComputed("student not loaded")` |
| Feature computation fails (an internal error) | `NotComputed(<fixed text>)`; never a default p |

`Computed` needs a `Provenance` and a `Window` (`domain/computed.py`). The values are a decision for the wiring
slice; a fitting choice is `source=ProvenanceSource.model`, `tier=Tier.C`, `model_id` naming the variant and the
ONNX sha256, `quality=ok`.

## 4. The reward policy (`PolicySignal`)

### 4.1 The six numbers

From `policy.json` `params` and `config` (content sha256 checked, see below):

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
must match the folder (true for `policy.json`, false for `no_temperature/`); `Params.from_dict` checks four finite
non-negative weights, theta finite and at least 0.1, p an integer from 1 to 8 (no bools, no string numbers);
`digest(config) == config_sha256`; a no-temperature policy must have temperature weight 0.0. Refuse otherwise.
Both shipped files pass this check (recomputed for this hand-off).

### 4.2 z, score and state

- z per vital at scored bin t (`reward_policy.z_series`): `B_v = baseline_finding(v, series_v, DEFAULTS)` on the
  same series as section 3.2. If a tuple: `z_v = max(0.0, bad_v * B_v[1].z)`. If `NotComputed` (forming, or the
  latest value missing): `z_v = 0.0`. This z is clipped at 0 in the bad direction.
- Bad direction (`baseline.VITALS[v].bad`): heart rate +1 (rising is bad), respiratory rate +1, SpO2 -1 (falling is
  bad), temperature +1.
- Score (form `"sum"`): `s = w_hr * z_hr + w_rr * z_rr + w_spo2 * z_spo2 + w_temp * z_temp`. (Form `"max"`, not
  shipped: the largest single weighted z.)
- Persistence: the run counts consecutive scored bins with `s >= theta`. A NOT RANKED bin ends the run and is not
  alerting. A missing bin (no row in that 15-minute slot) also ends the run. Alerting when `run >= p`.
- While a baseline forms, that vital contributes 0. With all four forming the score is 0, below theta (at least
  0.1), so the policy cannot alert in a stay's first 24 valid bins of every vital.

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

A gap resets the run but the bin after it is still scored: with `s >= theta` its run is 1.

### 4.3 What `PolicySignal` receives

| Case | `PolicySignal.alert` |
|---|---|
| NOT RANKED bin | `NotComputed("not ranked")`, or no `PolicySignal` (same decision as for `TierCSignal`). Research state is False |
| Scored bin, policy loaded and checked | `Computed(state[t])` |
| Scored bin, baselines forming | `Computed(False)` (research treats it as not alerting) |
| Policy file missing or refused | `NotComputed("policy not loaded")` |

### 4.4 Which variant to wire by default

This is a product decision, not the models session's. What the card (`models/tier_b/reward_policy/card.md`, from
`held_out.txt`, ward-v2 seeds 11-30, scored once) shows:

| Held-out | NEWS2 chart trigger | Final, with temperature | Final, no temperature |
|---|---|---|---|
| Alone: alerts per patient per 12 h (95% CI) | 0.414 (0.395-0.434) | 0.153 (0.140-0.165) | 0.131 (0.119-0.143) |
| Alone: time in alert | 0.158 | 0.035 | 0.021 |
| Alone: median lead (min) | 47.2 | -3.8 | 20.2 |
| NEWS2 OR policy: any-bin, all episodes (95% CI) | 0.626 (0.522-0.727) | 0.703 (0.608-0.798) | 0.692 (0.596-0.785) |
| NEWS2 OR policy: alerts per patient per 12 h | 0.414 | 0.463 | 0.451 |

- Added to NEWS2, neither policy gives a gain beyond noise: the intervals overlap. Both raise more alerts.
- With temperature, the policy alerts late (median lead -3.8 min). That is not early warning.
- No temperature scored higher on validation (1.343958 against 0.992414), but that comparison was not
  pre-registered and reuses validation.
- `policy.json` (with temperature) is the ledger's incumbent; the held-out command refuses any other file there.

## 5. The confidence check (`Guarantees`)

### 5.1 The check

`research/confidence.py` `check(p, t, q)`; q None means +infinity:

| Condition | Result | Student alert on this bin |
|---|---|---|
| `p < t` | `"quiet"` | no |
| `p >= t` and q is not None and `p > q` | `"alert"` | yes |
| `p >= t` and (q is None or `p <= q`) | `"not sure"` | no, never |

Mathematically the check is a second threshold, `max(t, q)`, with a strict `>` against q.

### 5.2 q per variant

| Variant | File | q | t | alpha | n, j, k | Binds ONNX sha256 |
|---|---|---|---|---|---|---|
| `all` | `models/tier_c/confidence_check/calibration.json` | 0.019854247570037842 | 0.006 | `"0.01"` | 3238, 32, 3207 | `d1ae4e881732922024b08c6cf9c37adbd8b01c48bc8390ee22b28ab802ddfdbc` |
| `no_temp` | `models/tier_c/confidence_check/calibration_no_temp.json` | 0.018539994955062866 | 0.006 | `"0.01"` | 3238, 32, 3207 | `e6b6ae44c11aa38fc38a961eebaf3e3bd51b1b5812b9e200d31d8c04101869ee` |

Both are binding (q >= t; code: `Calibration.binding` is `q is None or q >= t`). Calibration data: ward-v2-ext
seeds 301-340, one negative bin per non-starting stay, draw seed 20261113.

### 5.3 The file binds one model; refuse it on any difference

Port `confidence.load` and `confidence.verify`. Refuse the file, and treat every bin as having no supported alert,
when any of these fails:

- The file is at most 64 KiB and its sha256 equals the text of the sibling `<name>.sha256` (stripped).
- The field set is exactly `alpha, days, draw_seed, j, k, n, negative_bins, onnx_sha256, q, scenario_sha256, seeds,
  speed, t, variant`, with the code's types.
- `alpha == "0.01"` (a string) and `draw_seed == 20261113`.
- `(j, k)` equals `indices(n)` (`j = floor(alpha * (n + 1))` in exact fractions, `k = n + 1 - j`), and q is null
  exactly when j is 0.
- `variant` is the loaded student's variant; `onnx_sha256` equals `model.json` `source.onnx_sha256` (and the ONNX
  file's sha256 when it is loaded); `t` equals the student's threshold.
- `(days, speed, scenario_sha256)` equals the setting where it is used. Research compares it with the trial's
  manifest (30, 60, `95052d021b73da3fa71cc62601a795264b8ffd1cf5c4bf2e6e3ba407f74698a8`). A live product run has no
  such manifest: see "Decisions".

Retraining or re-exporting the student changes the ONNX sha256 and so invalidates q. A new q needs a new
calibration run (`python -m research.confidence calibrate`). Never copy q to another model.

### 5.4 What `Guarantees` receives

`Guarantees` has two fields, both `bool | None`. It has no per-bin field.

| Field | Value |
|---|---|
| `conformal_certificate_current` | True only when the calibration file passed every check in 5.3 against the loaded student. False when it was refused or the setting differs. None when the check is not wired |
| `drift_flagged` | None. Nothing shipped computes drift |

The per-bin result ("alert", "not sure", "quiet") has no slot in today's hooks. See "Decisions".

### 5.5 What "not sure" must do in the product

- It never raises an alert.
- It never raises a patient's rank or moves them up the list.
- Show it as "not sure". Never show it as "safe", "normal" or "low risk".
- It never suppresses a NEWS2 escalation. The check gates only the student's own alert. NEWS2's `escalated`, its
  reasons and its rank are computed without it.
- "Quiet" means only that the student rule did not fire. No confident-quiet claim is made.

### 5.6 What the target means, and what it does not

- In plain words: for a patient who is not starting to deteriorate, at most 1 in 100 of their 15-minute checks
  shows a supported alert, on average over stays, inside the simulator.
- It is not precision. Positives are about 0.3% of bins (0.3143% on validation), so most supported alerts are
  still false.
- It is not per patient, not per shift, and not a clinical guarantee: exchangeability holds only inside the
  simulator.
- Do not use the word "guarantee" in the UI. The evaluation exporter already bans "conformal guarantee", and any
  positive "validated".

## 6. How the three meet in the arbiter

### 6.1 The arbiter today (`arbitrate`, read as it is)

1. Rule 5 first: if NEWS2 abstains (`News2Abstained`), return `NotRanked` naming the missing inputs. Nothing else
   is looked at.
2. Rule 1: `escalated = tier_a.risk in {low_medium, medium, high}` (a single parameter at 3, a total of 5-6, a total
   of 7 or more). Reasons from NEWS2 only.
3. Rule 3: each significant `TierBSignal` finding adds a reason and sets `flagged`. `flagged` orders only patients
   with no trigger and an equal total; it never sets `escalated`.
4. Rule 2 (`TierCSignal`), the learned policy (`PolicySignal`), rule 4 and `Guarantees`: inert. Supplying one (any
   non-None value, `NotComputed` included) only adds its name (`rule_2_tier_c`, `learned_policy`, `guarantees`) to
   `inert`.
5. Ward order (`Ranked.sort_key`): trigger level, then NEWS2 total, then the Tier B flag (below a trigger only), then
   patient id.

### 6.2 Decision table

| NEWS2 result | Baseline finding | Student p | Policy state | Check | `TierCSignal.probability` | `PolicySignal.alert` | `Guarantees` | Arbiter result today |
|---|---|---|---|---|---|---|---|---|
| abstains | not computed | none | none | none | `NotComputed("not ranked")` or omitted | `NotComputed("not ranked")` or omitted | as 5.4 | `NotRanked`, unchanged except `inert` |
| any score | any | model not loaded | any | none | `NotComputed("student not loaded")` | as 4.3 | `conformal_certificate_current=False` or None | as NEWS2 and Tier B decide |
| any score | any | p < t | any | quiet | `Computed(p)` | `Computed(state)` | as 5.4 | as NEWS2 and Tier B decide |
| any score | any | t <= p <= q | any | not sure | `Computed(p)` | `Computed(state)` | as 5.4 | as NEWS2 and Tier B decide; never raised |
| any score | any | p > q (>= t) | any | alert | `Computed(p)` | `Computed(state)` | as 5.4 | as NEWS2 and Tier B decide |
| escalating risk | any | any | any | any | as above | as above | as above | `escalated = True`; no model can clear it |
| low risk | significant | any | any | any | as above | as above | as above | `escalated = False`, `flagged = True` (tie-break only) |

The invariant the script promises: no single model can push a patient up the list on its own. Today none of the
three can change `escalated`, `reasons`, `flagged` or the order; NEWS2 decides escalation, and Tier B only breaks
ties below a trigger. Any change to that is a new arbiter rule and belongs to the wiring slice's decisions.

## 7. The alert rule and abstention

If the product raises alerts from these signals, use the rule every evaluation used (`alerts.scan`):

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

- One open alert per patient. A new alert opens only after 16 bins (4 hours) not alerting.
- `alerting[m]` for the student: `p >= t` (or `check(p, t, q) == "alert"` with the check); for the policy:
  `state[m]`. A NOT RANKED bin and a missing bin are not alerting.
- Abstention cases, all of which give no model output and no alert: NOT RANKED bin; model or calibration file
  missing or refused; the variant's files not matching each other. A forming baseline or a missing lag is not
  abstention for the student (it still gives p), and gives a not-alerting state for the policy.
- What must never happen: imputing or carrying forward a value; scoring a NOT RANKED bin; reading a later bin; mixing
  the `all` student with the `no_temp` calibration (or the reverse); using q with any other ONNX; letting "not sure"
  alert; letting any of these change NEWS2's score, risk, reasons or `escalated`.

## 8. Files to load

Bytes from `stat`, sha256 from `shasum -a 256`; every model file matches `models/manifest.json`.

| Path | Bytes | sha256 | Role |
|---|---|---|---|
| `models/tier_c/student_vitals/model.json` | 34,949 | `d05d75ee0ad8da0db138a74f3a9796c98de6b8af5dfea513789d5521f1768af6` | student `all`, plain form |
| `models/tier_c/student_vitals/model_no_temp.json` | 14,903 | `d041b6584edc7d67c54dcdb03e81f0c1a58f51282a3cf02fbc3e869ff53cb2fc` | student `no_temp`, plain form |
| `models/tier_c/student_vitals/model.onnx` | 5,334 | `d1ae4e881732922024b08c6cf9c37adbd8b01c48bc8390ee22b28ab802ddfdbc` | binding only (not needed at run time) |
| `models/tier_c/student_vitals/model_no_temp.onnx` | 2,382 | `e6b6ae44c11aa38fc38a961eebaf3e3bd51b1b5812b9e200d31d8c04101869ee` | binding only |
| `models/tier_c/student_vitals/metadata.json` | 28,290 | `4ef6898ca6e9e2ca81b90377586642a3556ff7343bdd1c8f66dbad324ad25094` | record: names, threshold, sizes |
| `models/tier_b/reward_policy/policy.json` | 5,064 | `21cf038e4ab699906eeab87d56ddbd39965915a8ec085e75f56ca99501114775` | policy with temperature (content sha256 `b364a680...f804f`) |
| `models/tier_b/reward_policy/no_temperature/policy.json` | 4,916 | `522195aa8104271d3bfec0e87ab1f37e07b074dccbd5a347d05191905a12a3b3` | policy no temperature (content sha256 `ef60e564...e020`) |
| `models/tier_c/confidence_check/calibration.json` | 660 | `7815bd3885c794fafa38e8384d6837300c163738dbb6a0a7a126c4dca2484f11` | q for `all` |
| `models/tier_c/confidence_check/calibration.json.sha256` | 65 | `e4017fd23c40974d97d7807a377c37672735c9334c7d94dbece771ecccdef0cf` | holds the line above |
| `models/tier_c/confidence_check/calibration_no_temp.json` | 664 | `4a3de9544cce7e6cc838837bb2c1646261d85d1f41bef35f84135509fa0729bb` | q for `no_temp` |
| `models/tier_c/confidence_check/calibration_no_temp.json.sha256` | 65 | `484f8b1440da0715646ce7473e1f5ee3df010634ea2f0f15b2b9f436df053c55` | holds the line above |

Pairs to load together: `model.json` with `calibration.json`; `model_no_temp.json` with
`calibration_no_temp.json`. The policy variant is chosen on its own (section 4.4).

Reference implementations to port or copy:

| Code | What to take | Dependencies |
|---|---|---|
| `research/plain_student.py` | `parse`, `load`, `forward` (copy verbatim) | standard library |
| `research/student.py` | `patient_features`, `stay_features`, `_names`, `view` (port the arithmetic, do not import) | numpy, MindSpore at import: never import it in the backend |
| `research/reward_policy.py` | `z_series`, `states`, `Params` | standard library and the backend's `baseline_finding` |
| `research/reward_loop.py` | `load_policy`, `digest` | standard library |
| `research/confidence.py` | `indices`, `check`, `Calibration`, `load`, `verify` | standard library |
| `research/alerts.py` | `scan`, `REARM_BINS` | standard library |

No new backend dependency is needed: the plain JSON forward pass, the policy and the check use only the standard
library, and the baseline is already the backend's own `baseline_finding`.

## 9. Test plan for the wiring slice

1. Forward parity: the backend port against `research/plain_student.py` on the fixed batch of section 3.8, both
   variants.
2. Feature parity on recorded stays. Make a golden fixture once, in the research ML environment:
   `research.trials.load_trial("research/tests/data/toy")`, group rows by patient, then for each stay
   `minutes, x = research.student.stay_features(rows)` and
   `research.student.view(x.astype(numpy.float32), temp, "trend")` with temp True and False. Those rows are the
   expected inputs (35 and 27 columns) per scored minute; add the expected p from `plain_student.forward`. The
   toy stays are at most 14 bins long, so their baselines never form: also add one long stay built like
   `research/tests/test_reward.py` `stay()` (94 bins), with one gap and one NOT RANKED bin after bin 24. The
   backend computes the same rows from the same stored bins and must match each value exactly (after float32
   rounding).
3. Policy parity: on the same stays, the backend's states against
   `research.reward_policy.states(Params.from_dict(policy["params"]), research.reward_policy.z_series(rows), "sum")`,
   both shipped policies.
4. Check truth table: p below t; p equal to t; p between t and q; p equal to q (must be "not sure"); p above q; q
   None (never "alert").
5. "Not sure" never alerts: for every bin with `t <= p <= q`, no alert opens and the student's alerting state is
   False.
6. NOT RANKED and short history: a NOT RANKED bin gives no p and no policy state and resets the policy run; a
   missing bin resets the run; the first scored bin of a stay gives `Computed(p)` with z, d1h and d4h flags set;
   with no gaps, bin indices 4 and 16 (the 5th and 17th bins) are the first with a d1h and a d4h value.
7. File refusals: a changed byte in each file; a calibration `.sha256` that differs; a calibration for the other
   variant; a policy with a changed weight but the old `sha256`.
8. NEWS2 unchanged: for a ward fixture, the decisions with the hooks on and off have identical `news2`,
   `escalated`, `reasons`, `flagged` and rank; only `inert` may differ. The API's NEWS2 and rank output must be
   byte-identical (`inert` is not serialised today).
9. The alert rule: re-arm after exactly 16 not-alerting bins, missing bins counting as not alerting.

## 10. Limits and what was not checked

- Not run on a ward hub or a small ARM board. Feature computation in the backend has not been timed (only the
  ONNX call was timed in research).
- Circular synthetic data: one simulator makes the episodes, the vitals and the judgement. Every number is an upper
  bound inside the simulator, not a performance claim.
- Validation and held-out facts are in the cards: `models/tier_c/student_vitals/card.md`,
  `models/tier_b/reward_policy/card.md`, `models/tier_c/confidence_check/card.md`.
- The student never saw a non-alert consciousness level or a missing device vital in training (section 3.5).
- The simulator's bin-minute convention (start or end of the bin) was not checked here; it lives in the simulator
  repo.
- Backend and research differ in how the series is built (see "Decisions"); parity holds only if the wiring slice
  builds the series as section 3.2 says.

## Decisions for the wiring slice

1. Which variant pair to wire: student `all` with `calibration.json`, or `no_temp` with
   `calibration_no_temp.json`; and, separately, which policy file (section 4.4).
2. Whether a NOT RANKED bin gets `TierCSignal(NotComputed(...))` and `PolicySignal(NotComputed(...))` or no hook
   at all. Supplying a `NotComputed` lists the hook in `NotRanked.inert`.
3. Where the per-bin check result ("alert", "not sure", "quiet") is carried. `TierCSignal` holds only p and
   `Guarantees` holds two per-patient bools.
4. `conformal_certificate_current` on a live run: the calibration binds days 30, speed 60 and one scenario sha256.
   A live ward run has no such manifest, so the code's own rule says the guarantee cannot be claimed there.
   Decide whether the live run carries a comparable setting or the field stays False while the check still acts
   as the threshold `max(t, q)`.
5. Whether the product raises any alert from the student or the policy at all. If it does, where the per-patient
   alert state (the 16-bin re-arm counter) is stored, since `decide` is pure and recomputes on every read.
6. Which bin is "current": the backend scores NEWS2 on the live vitals, while research features use one bin's
   stored values and that bin's NEWS2 inputs. The policy and the alert rule also need per-bin scored status for
   earlier bins, which needs the nurse-entered NEWS2 inputs (BP, ACVPU, oxygen, SpO2 scale) as they were at each
   bin.
7. Series construction: backend `grid()` caps at 672 bins (7 days), starts at the first bin at or after admission,
   and drops physically impossible values (`possible()`); backend `counted()` skips a stale patch or a
   nurse-typed live value. Research uses every stored row of the stay with none of these. Decide which applies
   to the model features, and accept that stays over 7 days move the baseline anchor.
8. The `Provenance` and `Window` values inside each `Computed`.
9. Whether to round features to float32 before the forward pass (exact research parity) or pass double values
   (within about 1e-6 of research; a p within that distance of t or q could flip).
10. Whether p and the policy state are shown in the first hours of a stay, when the missing flags are set, or
    hidden until the baseline forms.
11. How each signal is labelled for clinicians ("synthetic, not validated"), and that "not sure" is shown as
    "not sure".
12. Any new arbiter rule that lets one of these signals change order or escalation. Not designed here.
