# Confidence check on the student (Tier C, research only)

| | |
|---|---|
| id | `tier_c/confidence_check` (variants `all` and `no_temp`) |
| files | `calibration.json` (660 bytes) and `calibration.json.sha256`; `calibration_no_temp.json` (664 bytes) and `calibration_no_temp.json.sha256`; `heldout.txt` (sha256 `a30cad7664c49dc4faa516ceaca335676bfc86a3bb9e0556ab3296a1d5da8abd`) and `heldout.json` |
| code | `research/confidence.py` (`calibrate`, `evaluate`) |
| model | the student `tier_c/student_vitals` (`model.onnx`, `model_no_temp.onnx`) and its threshold t, unchanged |
| run log | `training/runs/confidence_check/m3-20261113-fbd39f2.json` |
| status | calibrated on synthetic trials; held-out ward-v2 seeds 11-30 scored once on 2026-11-13; not validated, not wired to the API, the rank or the arbiter |
| intended use | research. Tier A (NEWS2) is never changed or learned |

Held-out ward-v2 seeds 11-30 were scored once, on 2026-11-13. The outputs are `heldout.txt` and `heldout.json` (format
`biosentry-held-out/1`, full precision). The pre-registered target was MET for both variants, inside the simulator.
Staying quiet removed about 6 in 10 of the student's alerting bins and lost 8 (`all`) and 9 (`no_temp`) episodes the
student was alerting in. Added to NEWS2, the checked student gives no gain beyond noise.

## What the pitch line says and what is true now

"On top of that there'll be a confidence check, so when the system isn't sure, it stays quiet."
- The check exists and is calibrated on ward-v2-ext seeds 301-340 (below).
- It binds for both variants. "Binding" means q is absent or q >= t; here q is above t, so some of the student's
  alerts become "not sure".
- "Not sure" never alerts. That is true by construction: a bin alerts only if p >= t and p > q.
- On held-out the achieved error was 0.0065 (`all`) and 0.0067 (`no_temp`) against alpha 0.01. The cost: 8 and 9
  episodes the student was alerting in, the checked rule is not.

"Nurses already get too many false alarms": the check does not fix that by itself. See the next section.

## What is controlled, in plain words, and what is not

- Controlled: for a patient who is not starting to deteriorate, at most 1 in 100 of their 15-minute checks shows a
  supported alert, on average over stays (alpha 0.01). This holds by design, inside the simulator.
- Not controlled: the share of alerts that are true. About 0.3% of scored bins are positive (0.2735% training,
  0.3143% validation). So even when the target is met, most supported alerts are false.
- One-sided only. "Quiet" means only that the rule did not fire. No confident-quiet claim is made.

## Method (pre-registered in `research/README.md`, slice M3, before any run)

- Score: s = the student's p on a scored (RANKED) bin, from the committed ONNX. The ONNX sha256 and t are copied into
  the calibration file.
- Unit: one negative bin (label 0, the student's label rule) per non-starting stay that has one, drawn uniformly with
  SplitMix64, draw seed 20261113. The draw visits stays by sorted seed, then in `episodes.csv` order. The order does
  not matter: any order fixed before the scores are seen gives one uniform draw per stay. One draw per stay gives
  exchangeable scores whatever the dependence between bins of a stay ("subsampling once", Dunn, Wasserman, Ramdas).
- Quantile: j = floor(alpha (n + 1)) in exact fractions, k = n + 1 - j, q = the k-th smallest score. Supported iff
  p > q (strict, so ties are conservative). Rule: alert iff p >= t and p > q; "not sure" iff p >= t and p <= q;
  "quiet" iff p < t.
- alpha = 0.01, fixed in code (`ALPHA = "0.01"`) before the run: below the old student's time in alert (about 2%), so
  it can bind, and far below NEWS2's (about 16%).
- The calibration file binds: the variant, the student's ONNX sha256 and t, the trial days, speed and scenario sha256,
  the seeds (exactly 301-340), alpha and the draw seed. `evaluate` refuses the file if its sha256 differs from its
  `.sha256` sibling, if its field set is not exactly the expected one, or if j, k and q do not follow from n.

## Calibration (ward-v2-ext seeds 301-340, all 40 trials; 30 days, speed 60, scenario sha256 `95052d02...98a8`)

| | `all` | `no_temp` |
|---|---|---|
| stays drawn, n (one negative bin each) | 3,238 | 3,238 |
| negative scored bins in those stays | 1,257,362 | 1,257,362 |
| j, k | 32, 3,207 | 32, 3,207 |
| q | 0.019854247570037842 | 0.018539994955062866 |
| t (from the student) | 0.006 | 0.006 |
| binding (q absent or q >= t) | yes | yes |
| student ONNX sha256 | `d1ae4e88...ddfdbc` | `e6b6ae44...69ee` |
| calibration file sha256 | `7815bd3885c794fafa38e8384d6837300c163738dbb6a0a7a126c4dca2484f11` | `4a3de9544cce7e6cc838837bb2c1646261d85d1f41bef35f84135509fa0729bb` |

Checked by a second agent before any held-out run: the code's decisions matched its own implementation of the
conformal p-value on 389,123 cases, with 0 mismatches. In its simulation, exchangeable scores gave coverage 0.0098 to
0.0101 at alpha 0.01. With strongly dependent bins inside stays, one bin per stay held 0.0100, while pooling all bins
reached 0.053 when long stays score lower. Before the held-out reads it also recomputed the calibration on its own:
n 3,238, j 32, k 3,207 and q equal the files to full precision for both variants. There are 3,241 non-starting
calibration stays; 3 have no negative bin. There are 1,257,362 negative bins. Its preview on the VALIDATION seeds 261-300 (not held-out):
the stay-weighted share of negative bins with p > q is 0.00608 (`all`) and 0.00644 (`no_temp`). On the calibration
stays themselves it is 0.00720 and 0.00765.

## Held-out results (ward-v2 seeds 11-30, scored once on 2026-11-13)

1,632 held-out non-starting stays with a negative scored bin. The achieved error is the stay-weighted mean share of
negative (non-onset) scored bins with a supported alert (p > q), with a 95% stay-bootstrap interval (1,000 resamples,
seed 20261112). MET iff the lower end is at most alpha + d, d = 2 sqrt(alpha (1 - alpha) / n).

| | `all` | `no_temp` |
|---|---|---|
| achieved error (95% interval) | 0.0065 (0.0057 to 0.0074) | 0.0067 (0.0058 to 0.0076) |
| bound alpha + d (d 0.0035) | 0.0135 | 0.0135 |
| verdict | MET | MET |
| same share, student alone (p >= t) | 0.0407 (0.0380 to 0.0440) | 0.0417 (0.0388 to 0.0449) |
| context only, pooled over bins: p > q | 0.0068 (4,072/599,144) | 0.0070 (4,181/599,144) |
| context only, pooled over bins: student alone | 0.0432 (25,854/599,144) | 0.0439 (26,323/599,144) |
| "not sure", share of scored bins | 0.0415 (27,523/663,089) | 0.0434 (28,792/663,089) |
| "not sure", share of the student's alerting bins | 0.5970 (27,523/46,103) | 0.6142 (28,792/46,875) |
| alerting bins, student to checked | 46,103 to 18,580 | 46,875 to 18,083 |
| new alerts, student to checked | 6,247 to 1,930 | 6,188 to 1,926 |
| episodes lost, all evaluated: any-bin / caught early | 8 / 13 | 9 / 13 |
| episodes lost, not cut short: any-bin / caught early | 8 / 13 | 9 / 13 |

"Lost" is an episode the student detects and the checked rule does not. "Caught early" is a new alert in the window
before fully in. The pooled figures are context; the pre-registered quantity is the stay-weighted one.

Columns as in the student card (91 episodes, 79 not cut short, 4,678 control windows, 1,873 patients); New-alert
excess is the new-alert rate over episodes not cut short minus `chance_new`.

| Policy | Alerts/pt/12 h (95% CI) | Time in alert | Any-bin | Any-bin, all (95% CI) | Chance | Excess | New-alert excess | Lead |
|---|---|---|---|---|---|---|---|---|
| NEWS2 chart trigger | 0.414 (0.395-0.434) | 0.158 | 0.684 | 0.626 (0.522-0.727) | 0.473 | +0.210 | +0.179 | 47.2 |
| Student | 0.413 (0.394-0.430) | 0.064 | 0.734 | 0.670 (0.570-0.762) | 0.319 | +0.415 | +0.389 | 59.5 |
| Student checked | 0.128 (0.116-0.140) | 0.026 | 0.633 | 0.582 (0.473-0.681) | 0.089 | +0.544 | +0.556 | 20.5 |
| NEWS2 OR student | 0.525 (0.507-0.543) | 0.185 | 0.823 | 0.747 (0.655-0.831) | 0.580 | +0.243 | +0.209 | 66.0 |
| NEWS2 OR student checked | 0.438 (0.418-0.457) | 0.165 | 0.759 | 0.692 (0.593-0.782) | 0.491 | +0.268 | +0.241 | 44.6 |
| Student `no_temp` | 0.409 (0.391-0.427) | 0.065 | 0.747 | 0.681 (0.576-0.773) | 0.329 | +0.418 | +0.391 | 48.8 |
| Student checked `no_temp` | 0.127 (0.115-0.139) | 0.025 | 0.633 | 0.582 (0.473-0.681) | 0.091 | +0.542 | +0.530 | 16.1 |
| NEWS2 OR student `no_temp` | 0.512 (0.494-0.530) | 0.183 | 0.823 | 0.747 (0.651-0.835) | 0.577 | +0.246 | +0.214 | 66.0 |
| NEWS2 OR student checked `no_temp` | 0.433 (0.413-0.452) | 0.165 | 0.759 | 0.692 (0.593-0.782) | 0.491 | +0.268 | +0.242 | 44.6 |

Chance rates and excess have no interval. Per-kind rows are in `heldout.txt`.

What this shows:
- The target was met on held-out, inside the simulator. The achieved error is 0.0065 (0.0057 to 0.0074) for `all`
  and 0.0067 (0.0058 to 0.0076) for `no_temp`, against alpha 0.01. Both are below alpha, and both whole intervals are
  below alpha. The pre-registered rule compares the lower end with 0.0135, so MET.
- The run does not call the result "conservative". In the code that word means the upper end is below alpha - d
  (0.0065). The upper ends, 0.0074 and 0.0076, are not. The same share is also below alpha on the calibration stays
  themselves (0.00720 and 0.00765, second agent), so a value below alpha is not special to held-out.
- What staying quiet costs (`all`): it removes 27,523 of 46,103 alerting bins (0.597) and 4,317 of 6,247 new alerts.
  Alerts per patient per 12 h fall from 0.413 to 0.128. It loses 8 episodes the student was alerting in (any-bin over
  all episodes 0.670 to 0.582) and 13 episodes the student caught early. The checked rule's median lead is 20.5
  minutes against the student's 59.5. `no_temp` is alike: 28,792 of 46,875 bins, 9 and 13 episodes.
- Added to NEWS2, the checked student gives no gain beyond noise. NEWS2 OR student checked alerts in 0.692
  (0.593-0.782) of all episodes against NEWS2's 0.626 (0.522-0.727). The intervals overlap. It costs 1.06 times NEWS2's
  alerts (0.438 against 0.414), with chance 0.491 against 0.473 and excess +0.268 against +0.210. `no_temp`: 0.692
  (0.593-0.782) at 0.433 alerts, 1.04 times.
- Hypotensive episodes (27; 17 with no cuff reading inside the window): the checked student is alerting in 0 of 27,
  any bin and new alert, for both variants. The unchecked student: any bin 7 and new alert 5 (`no_temp` 9 and 6).
  NEWS2: 9 and 2. NEWS2 OR student checked: 9 and 2 for both variants, the same as NEWS2 alone. So the check removes
  exactly the student's weak, low-probability alerts, and those are all it had in hypotensive episodes. These are the
  episodes the vitals show least: a blood pressure appears only when a nurse takes a cuff reading, and the student
  sees it only through the NEWS2 total. Few events (27).
- Most supported alerts are still false. A bin-level precision cannot be read from these outputs: the alerting-bin
  counts include starting patients and bins after fully in, which have no label here. At the alert level, the
  checked student raises 37.1 alerts per episode with a new alert in its window (`no_temp` 38.5); NEWS2 raises 190.0.

What it cannot show:
- Anything about patients. These are simulated wards. Exchangeability holds only inside the simulator.
- Whether the target holds on another simulator setting or on a live ward run.

## Limits

- Exchangeability holds only inside the simulator, and stays in one trial are dependent. This is not a clinical
  guarantee: synthetic, not validated.
- The control is weighted by stay, not by bin. The "pooled over bins" figure counts long stays more, so it can sit
  above alpha while the target holds (see the simulation above: 0.0100 per stay, 0.053 pooled). On held-out the two
  are close (0.0065 and 0.0068).
- It is marginal, over the calibration draw, the new stay and the bin. It is not per patient and not per shift.
- It bounds false alerts, not precision. Mathematically the check is a second threshold, max(t, q); its only
  addition is that q has a stated finite-sample meaning.
- One random draw per stay: another draw seed gives a slightly different q.
- The calibration binds 30-day trials at speed 60 and one scenario sha256. By the code's own rule (`verify`), the
  target cannot be claimed on a live ward run.
- `evaluate` refuses a second held-out run while `heldout.txt` exists. That is a guard by procedure: deleting the file
  removes it. The same held-out seeds were used earlier for earlier models of this project; by procedure this check
  was read on them once. Held-out was not used to choose anything.
- Not wired to the API, the rank or the arbiter.

## Reproduce

Inside the research venv (`research/README.md`, ML-GATE), in a shell with brace expansion:

    python -m research.confidence calibrate /data/trials/ward-v2-ext/seed-{301..340} --run_log training/runs/confidence_check/<run_id>.json --run_id <run_id> --commit <sha>
    python -m research.confidence evaluate <ward-v2 folders of seeds 11-30> --json models/tier_c/confidence_check/heldout.json

The evaluate command was run once, on 2026-11-13. Do not run it again: it reads the held-out seeds.

Packages (from the run log): Python 3.11.18, numpy 2.4.6, onnxruntime 1.31.1.

## References

- Vovk, Gammerman, Shafer (2005), Algorithmic Learning in a Random World, Springer.
  https://link.springer.com/book/10.1007/b106715
- Angelopoulos, Bates (2021), A Gentle Introduction to Conformal Prediction. https://arxiv.org/abs/2107.07511
- Dunn, Wasserman, Ramdas (2022), Distribution-free prediction sets for two-layer hierarchical models, JASA.
  https://arxiv.org/abs/1809.07441
