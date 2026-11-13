# Confidence check on the student (Tier C, research only)

| | |
|---|---|
| id | `tier_c/confidence_check` (variants `all` and `no_temp`) |
| files | `calibration.json` (660 bytes) and `calibration.json.sha256`; `calibration_no_temp.json` (664 bytes) and `calibration_no_temp.json.sha256` |
| code | `research/confidence.py` (`calibrate`, `evaluate`) |
| model | the student `tier_c/student_vitals` (`model.onnx`, `model_no_temp.onnx`) and its threshold t, unchanged |
| run log | `training/runs/confidence_check/m3-20261113-fbd39f2.json` |
| status | calibrated on synthetic trials, held-out not yet scored, not validated, not wired to the API, the rank or the arbiter |
| intended use | research. Tier A (NEWS2) is never changed or learned |

Held-out not yet scored. The check is calibrated, but its achieved error and its cost in missed episodes are not known.
No held-out number of this check exists.

## What the pitch line says and what is true now

"On top of that there'll be a confidence check, so when the system isn't sure, it stays quiet."
- The check exists and is calibrated on ward-v2-ext seeds 301-340 (below).
- It binds: q is above t for both variants, so some of the student's alerts become "not sure".
- "Not sure" never alerts. That is true by construction: a bin alerts only if p >= t and p > q.
- What it costs in missed episodes, and what error it achieves on held-out, is NOT known yet. Held-out not yet scored.

"Nurses already get too many false alarms": the check does not fix that by itself. See the next section.

## What is controlled, in plain words, and what is not

- Controlled: for a patient who is not starting to deteriorate, at most 1 in 100 of their 15-minute checks shows a
  supported alert, on average over stays (alpha 0.01). This holds by design, inside the simulator.
- Not controlled: the share of alerts that are true. About 0.3% of bins are positive (0.3143% on the student's
  validation seeds). So even when the target is met, most supported alerts will still be false.
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

## Calibration (ward-v2-ext seeds 301-340, all 40 trials; 30 days, speed 60, scenario sha256 `95052d02...98a8`)

| | `all` | `no_temp` |
|---|---|---|
| stays drawn, n (one negative bin each) | 3,238 | 3,238 |
| negative scored bins in those stays | 1,257,362 | 1,257,362 |
| j, k | 32, 3,207 | 32, 3,207 |
| q | 0.019854247570037842 | 0.018539994955062866 |
| t (from the student) | 0.006 | 0.006 |
| binding (q > t) | yes | yes |
| student ONNX sha256 | `d1ae4e88...ddfdbc` | `e6b6ae44...69ee` |
| calibration file sha256 | `7815bd3885c794fafa38e8384d6837300c163738dbb6a0a7a126c4dca2484f11` | `4a3de9544cce7e6cc838837bb2c1646261d85d1f41bef35f84135509fa0729bb` |

Checked by a second agent before any run: the code's decisions matched its own implementation of the conformal
p-value on 389,123 cases, with 0 mismatches. In its simulation, exchangeable scores gave coverage 0.0098 to 0.0101 at
alpha 0.01. With strongly dependent bins inside stays, one bin per stay held 0.0100, while pooling all bins reached
0.053 when long stays score lower. The second agent's own recomputation of n, j, k and q from the real calibration
seeds was NOT finished.

## What will be reported on held-out, once (ward-v2 seeds 11-30)

Held-out not yet scored. The pre-registered report: rows `student`, `student_checked`, `news2_or_student`,
`news2_or_student_checked` (and `_no_temp`) next to NEWS2; (1) the achieved error, the mean over held-out
non-starting stays of each stay's share of negative scored bins with p > q, with a 95% stay-bootstrap interval
(1,000 resamples, seed 20261112); (2) the "not sure" share of scored bins and of the student's alerting bins; (3)
alerting bins and new alerts, student against checked; (4) episodes the student detects and the checked rule does
not; (5) n, k, q, t, alpha and the file's sha256.

MET or NOT MET (fixed before any run): d = 2 sqrt(alpha (1 - alpha) / n), about 0.0035 for n 3,238. MET iff the lower
end of the interval in (1) is at most alpha + d. Otherwise NOT MET, which is a finding, not a defect.

## Limits

- Exchangeability holds only inside the simulator, and stays in one trial are dependent. This is not a clinical
  guarantee: synthetic, not validated.
- The guarantee is weighted by stay, not by bin. The "pooled over bins" figure counts long stays more, so it can sit
  above alpha while the guarantee holds (see the simulation above: 0.0100 per stay, 0.053 pooled).
- It is marginal, over the calibration draw, the new stay and the bin. It is not per patient and not per shift.
- It bounds false alerts, not precision. Mathematically the check is a second threshold, max(t, q); its only
  addition is that q has a stated finite-sample meaning.
- One random draw per stay: another draw seed gives a slightly different q.
- `evaluate` refuses a second held-out run while `heldout.txt` exists. That is a guard by procedure: deleting the file
  removes it.
- Not wired to the API, the rank or the arbiter.

## Reproduce

Inside the research venv (`research/README.md`, ML-GATE), in a shell with brace expansion:

    python -m research.confidence calibrate /data/trials/ward-v2-ext/seed-{301..340} --run_log training/runs/confidence_check/<run_id>.json --run_id <run_id> --commit <sha>
    python -m research.confidence evaluate <ward-v2 folders of seeds 11-30>   # not run yet

Packages (from the run log): Python 3.11.18, numpy 2.4.6, onnxruntime 1.31.1.

## References

- Vovk, Gammerman, Shafer (2005), Algorithmic Learning in a Random World, Springer.
  https://link.springer.com/book/10.1007/b106715
- Angelopoulos, Bates (2021), A Gentle Introduction to Conformal Prediction. https://arxiv.org/abs/2107.07511
- Dunn, Wasserman, Ramdas (2022), Distribution-free prediction sets for two-layer hierarchical models, JASA.
  https://arxiv.org/abs/1809.07441
