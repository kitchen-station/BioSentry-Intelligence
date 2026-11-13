# Reward-driven alerting policy (Tier B, research only)

| | |
|---|---|
| id | `tier_b/reward_policy` (and `tier_b/reward_policy/no_temperature`) |
| files | `policy.json` (the incumbent); `rounds/round-1/` and `rounds/round-2/` (`gen-NN.json`, `policy.json`); `no_temperature/` (`generations/gen-00.json` to `gen-04.json`, `policy.json`); `experiments.jsonl` (the ledger); `held_out.txt` (the held-out output, verbatim) |
| code | `research/reward_policy.py` (policy, reward), `research/reward_loop.py` (inner loop, outer loop, held-out run) |
| status | learned on synthetic trials, not validated. Not wired to the API, the rank or the arbiter |
| intended use | research. Tier A (NEWS2) is never changed or learned |

Earlier ward-v1 and B6 results are in git history before this change.

## Result

- Learned: one accepted step. In round 1, generation 2 passed the fresh-batch test; nothing
  else did. Round 2 (16 selection trials) accepted nothing and was dropped. The outer loop
  stopped after 2 of the 4 allowed rounds: the research step read the records and proposed no
  third change.
- Held-out, added to NEWS2: no gain beyond chance is shown. NEWS2 OR final alerts in 7 more of
  91 episode windows (64 against 57) at 1.12 times NEWS2's alerts (0.463 against 0.414 per
  patient per 12 h). Chance rises from 0.473 to 0.528; excess is +0.244 against +0.210.
- Held-out, alone: a quiet rule. 0.153 alerts per patient per 12 h (NEWS2 0.414), time in alert
  0.035 (NEWS2 0.158), any-bin 0.633 (NEWS2 0.684), median lead -3.8 min (NEWS2 +47.2). Its
  excess (+0.493) is above NEWS2's (+0.210), but excess is capped at 1 minus chance (chance 0.140
  against 0.473), so rows with different chance cannot be ranked by excess.
- The old loop's ward-v2 policy sat in about the same place: 0.150 alerts, any-bin 0.646, chance
  0.120, excess +0.525, lead -11.2.
- No temperature: generation 1 was accepted. Held-out alone: 0.131 alerts, time in alert 0.021,
  any-bin 0.608, lead +20.2. NEWS2 OR it: 6 more of 91 windows (63 against 57) at 1.09 times
  NEWS2's alerts; chance 0.504, excess +0.256. No gain beyond chance is shown.
  On validation it scored above the with-temperature policy (1.343958 against 0.992414). That
  comparison was not pre-registered and reuses validation.

## What the pitch line says and what is true now

- "Points for every real case caught early, points off for every extra alarm": true. That is
  the reward, unchanged (below).
- "Thirty slightly different versions of itself": true. 30 candidates per generation.
- "Keeps the best one": true, with a condition. The best on the selection batch is kept only if
  it also beats the current policy on a second batch of fresh trials by more than noise.
- "Starts again from there": true. The next generation starts from the winner (or from the
  current policy if nothing was accepted).
- "Each time on patients it has never seen": true. Every generation uses trials no earlier
  generation saw. Synthetic patients only.
- "Recursive": true in two senses. The inner loop resumes from its own files and runs until its
  stop rule. An outer loop changes one search setting per round and records it in a ledger.
- "Learn by reward, the idea behind reinforcement learning": a (1+30) evolution strategy with a
  reward. It is not deep or per-step reinforcement learning: no state-action value, no per-step
  credit, no gradient.
- "Runs months on the ward": each trial is 30 simulated days of one ward. A generation used 20
  trials (8 selection, 12 confirmation) in round 1 and the no-temperature run, and 28 (16 and 12)
  in round 2. Every generation's policy is also scored on the 60 validation trials.

## A person signs off before anything changes

The loop only writes files on a branch. Nothing reaches main or the product without a reviewed
pull request. The policy is not wired to the API, the rank or the arbiter.

## Data and seed schedule

Synthetic only. Trials: `ward-v2-ext` (data card `models/data/ward-v2-ext/card.md`), 30 simulated
days each, more draws from the ward-v2 generator.

- Validation: seeds 341-400 (60 trials, 277 evaluated episodes).
- Round 1: seeds 401-500. Round 2: seeds 501-584. No temperature: seeds 401-480, sharing the
  round-1 schedule (allowed by the contract).
- Each seed is used once in the with-temperature schedule. Confirmation seeds come after their
  generation's selection seeds.
- Held-out: ward-v2 seeds 11-30 (sim commit f7647c5), scored once at the end.
- Circular: the episodes, the vitals and the judging all come from one simulator.

## Reward (unchanged)

Per trial: C - 0.5 x E x (a / a_NEWS2). C = evaluated episodes with the policy alerting in a bin
from episode start to fully in. E = evaluated episodes. a = the policy's alerts per patient per
12 h; a_NEWS2 = the NEWS2 chart trigger's on the same trial. lambda = 0.5, never tuned. Hard rule:
a policy whose time in alert is above NEWS2's on any trial of a set is rejected on that set.
`outcome` (higher care) is never part of the reward.

Amendment 1 (after round 1, before any round-2 result): a trial with no evaluated episode scores
0 for every policy, by the formula itself; the time-in-alert rule still applies. Round 2 had been
refused at seed 509. Seeds 509 and 517 are the only trials in 341-584 with no evaluated episode.
No seed was skipped. Round 1 had no such trial, so its files are unchanged.

## Inner loop

Policy: six numbers. Four non-negative weights (heart rate, respiratory rate, SpO2, temperature)
on each vital's z from the patient's own baseline, a threshold theta and a persistence p. It
alerts when the weighted sum is at least theta in p scored bins in a row. Generation 0: weights
1, 1, 1, 1 (no temperature: 1, 1, 1, 0), theta 3.0, p 4.

Per generation:
1. Draw 30 candidates around the current policy.
2. Selection: score the current policy and all 30 on the selection trials (8; 16 in round 2).
   Drop candidates rejected for time in alert. If the best mean is not strictly above the current
   policy's, nothing is accepted. Selection numbers are never used to accept (winner's curse).
3. Confirmation: score the winner and the current policy on 12 fresh trials. d_i = winner minus
   current on trial i. Accept if and only if the winner is not rejected for time in alert and
   mean(d) > 1.796 x stdev(d) / sqrt(12) (one-sided paired t-test at 5%, stdev with n - 1).
4. A winner that fails is not replaced by the second best. The generation accepts nothing.

Stop rule, fixed before the run: stop when the validation reward has not improved for 3
generations, or at 40 generations, whichever is first. The round ships the generation that last
improved.

Step size: 0.5 for weights and theta, 1.0 for p, at the start of every round. Multiplied by 0.85
after a generation that accepts nothing, kept after an accepted one. Floors 0.1 and 0.25.

Power note (pilot on ward-v2-train seeds 31-60 only). Paired SD 0.75. The confirmation margin is
about 0.39 reward units, about 8.7 more episodes caught early per 100 at equal alerts. False
accept per generation with no true difference: 5% by design. Power: 45%, 76% and 95% for true
gains of 0.3, 0.5 and 0.75. The 60 validation trials tell apart a difference of about 0.19.

## Learning curves (from the generation records)

Selection = winner mean vs current mean on the selection trials; rej = candidates (of 30)
rejected for time in alert. Confirmation = mean d / stdev d / threshold, on 12 trials. Validation =
seeds 341-400. Sigma = step used for weights and theta / for p.

Round 1 (default configuration, from generation 0):

| Gen | Seeds (sel / conf) | Selection | Rej | Confirmation | Accepted | Validation | Patience | Sigma |
|---|---|---|---|---|---|---|---|---|
| 0 | - | - | - | - | - | rejected | 0 | - |
| 1 | 401-408 / 409-420 | 0.830055 vs -0.488616 | 14 | 0.319833 / 1.187824 / 0.61584 | no | rejected | 1 | 0.5 / 1.0 |
| 2 | 421-428 / 429-440 | 0.291459 vs -0.461311 | 16 | 0.830741 / 0.592253 / 0.30706 | yes | 0.992414 | 0 | 0.425 / 0.85 |
| 3 | 441-448 / 449-460 | 1.3606 vs 1.149715 | 0 | -0.010871 / 0.552706 / 0.286556 | no | 0.992414 | 1 | 0.425 / 0.85 |
| 4 | 461-468 / 469-480 | 1.004686 vs 0.628914 | 0 | -0.334968 / 1.126274 / 0.583929 | no | 0.992414 | 2 | 0.36125 / 0.7225 |
| 5 | 481-488 / 489-500 | 0.999067 vs 1.07831 | 1 | not tested | no | 0.992414 | 3 (stop) | 0.3070625 / 0.614125 |

Generation 0 with temperature breaks NEWS2's time in alert on 7 of the 60 validation trials.

Round 2 (`selection_trials` 8 to 16, from the round-1 policy):

| Gen | Seeds (sel / conf) | Selection | Rej | Confirmation | Accepted | Validation | Patience | Sigma |
|---|---|---|---|---|---|---|---|---|
| 0 | - | - | - | - | - | 0.992414 | 0 | - |
| 1 | 501-516 / 517-528 | 0.970815 vs 0.732357 | 1 | 0.196165 / 0.612199 / 0.317401 | no | 0.992414 | 1 | 0.5 / 1.0 |
| 2 | 529-544 / 545-556 | 0.893381 vs 0.649859 | 0 | 0.327919 / 0.654072 / 0.339111 | no | 0.992414 | 2 | 0.425 / 0.85 |
| 3 | 557-572 / 573-584 | 0.717717 vs 0.680123 | 2 | 0.123924 / 0.436764 / 0.226445 | no | 0.992414 | 3 (stop) | 0.36125 / 0.7225 |

No temperature (default configuration, temperature weight fixed at 0):

| Gen | Seeds (sel / conf) | Selection | Rej | Confirmation | Accepted | Validation | Patience | Sigma |
|---|---|---|---|---|---|---|---|---|
| 0 | - | - | - | - | - | 0.785617 | 0 | - |
| 1 | 401-408 / 409-420 | 0.999889 vs 0.08083 | 6 | 0.943192 / 1.028013 / 0.532984 | yes | 1.343958 | 0 | 0.5 / 1.0 |
| 2 | 421-428 / 429-440 | 0.712292 vs 0.237418 | 1 | -0.021926 / 0.935766 / 0.485158 | no | 1.343958 | 1 | 0.5 / 1.0 |
| 3 | 441-448 / 449-460 | 1.432399 vs 1.229555 | 0 | -0.086718 / 0.72782 / 0.377346 | no | 1.343958 | 2 | 0.425 / 0.85 |
| 4 | 461-468 / 469-480 | 1.145605 vs 0.995927 | 0 | -0.458387 / 1.124651 / 0.583087 | no | 1.343958 | 3 (stop) | 0.36125 / 0.7225 |

In round 1, three winners led on 8 selection trials and lost the lead on 12 fresh ones. In round
2, with 16 selection trials, the selection lead and the confirmation mean agreed in sign, but no
confirmation reached its threshold.

## The ledger (`experiments.jsonl`)

- Round 1, baseline. The default configuration. Seeds 401-500, 5 generations, stopped by
  patience. Shipped generation 2: weights heart rate 0.614, respiratory rate 0.5247, SpO2 1.0638,
  temperature 0.3149; theta 2.9776; p 5. Validation reward 0.992414. Kept as the first incumbent.
- Round 2, dropped. Changed `selection_trials` from 8 to 16, because in round 1 the selection
  winners' leads vanished on the confirmation trials (the winner's-curse pattern). Seeds 501-584,
  3 generations, nothing accepted, stopped by patience. It shipped its generation 0, the
  incumbent's own numbers, so against the incumbent: mean 0.0, stdev 0.0, margin 0.0. Dropped; the
  incumbent stays.
- Stop. No change proposed: round 2's winners were confirmed slightly better than the incumbent
  (mean d +0.20, +0.33, +0.12) but below their thresholds, which points to a plateau, not to any
  setting on the menu.
- No temperature, learned once with the kept (default) configuration. Seeds 401-480, 4
  generations, stopped by patience. Shipped generation 1: weights heart rate 0.3372, respiratory
  rate 0.5534, SpO2 1.2459, temperature 0.0; theta 3.3703; p 3. Validation reward 1.343958.

Policy sha256 (of the file's bytes, as the ledger records them; the `sha256` field inside each
file is of its content and differs):

| File | File sha256 | Content sha256 |
|---|---|---|
| `policy.json` and `rounds/round-1/policy.json` | `21cf038e4ab699906eeab87d56ddbd39965915a8ec085e75f56ca99501114775` | `b364a680b989a659ae747f929806a65a9f828a0e412ba89d35ca72dd840f804f` |
| `rounds/round-2/policy.json` | `6a07e8939c7556c41e58620eb203507f2d52c355823c773b97c01d2bcdde7de4` | `cf33c1d17c5a0b73d1156f71cc7e11a662bb49f325b39abfdf2b86ef4afcb3b4` |
| `no_temperature/policy.json` | `522195aa8104271d3bfec0e87ab1f37e07b074dccbd5a347d05191905a12a3b3` | `ef60e564de8e3176d87fe78eabc3fc366e6b802ff0a9034e46122dc78090e020` |

## Validation (seeds 341-400, reused)

Checked by a second agent that did not build it, on the 60 validation trials (277 evaluated
episodes):

- With temperature (shipped): 115 caught early, 0.1599 alerts per patient per 12 h, time in alert
  0.0361.
- No temperature: 128 caught early, 0.1388 alerts, time in alert 0.0223.
- NEWS2: 0.4118 alerts, time in alert 0.1664.
- Paired difference, no temperature minus with temperature: mean 0.3515, stdev 0.7105, 2 x SE
  0.1834, better on 48 of 60 trials. Not pre-registered, and validation was reused.

## Held-out numbers (ward-v2 seeds 11 to 30, scored once)

91 episodes, 79 not cut short, 4,678 chance windows, 1,873 patients.

- Any-bin: alerting in any bin of the window, episodes not cut short. Any-bin, all: over all
  evaluated episodes, with its 95% interval.
- Chance and excess: for any-bin, episodes not cut short. Excess = any-bin minus chance.
- New-alert excess: new alert minus its own chance, episodes not cut short.
- Lead: median minutes before fully in, episodes with a new alert.

| Policy | Alerts/pt/12 h (95% CI) | Time in alert | Any-bin | Any-bin, all (95% CI) | Chance | Excess | New-alert excess | Lead |
|---|---|---|---|---|---|---|---|---|
| NEWS2 chart trigger | 0.414 (0.395-0.434) | 0.158 | 0.684 | 0.626 (0.522-0.727) | 0.473 | +0.210 | +0.179 | 47.2 |
| B4 baseline alone (k 4.0, p 8) | 0.042 (0.035-0.049) | 0.012 | 0.278 | 0.264 (0.174-0.360) | 0.032 | +0.246 | +0.253 | -82.5 |
| NEWS2 OR B4 | 0.426 (0.407-0.444) | 0.165 | 0.722 | 0.659 (0.558-0.758) | 0.486 | +0.235 | +0.210 | 39.6 |
| B4 alone, without temperature | 0.032 (0.026-0.038) | 0.011 | 0.266 | 0.253 (0.165-0.343) | 0.025 | +0.241 | +0.248 | -85.3 |
| NEWS2 OR B4, without temperature | 0.425 (0.405-0.442) | 0.164 | 0.709 | 0.648 (0.550-0.745) | 0.484 | +0.225 | +0.199 | 44.6 |
| Reward gen 0, with temperature | 0.429 (0.414-0.443) | 0.123 | 0.785 | 0.725 (0.625-0.816) | 0.435 | +0.350 | +0.301 | 23.0 |
| NEWS2 OR gen 0, with temperature | 0.535 (0.520-0.550) | 0.248 | 0.861 | 0.802 (0.716-0.885) | 0.666 | +0.195 | +0.084 | 67.9 |
| Reward final (round 1 gen 2), with temperature | 0.153 (0.140-0.165) | 0.035 | 0.633 | 0.571 (0.458-0.670) | 0.140 | +0.493 | +0.486 | -3.8 |
| NEWS2 OR final, with temperature | 0.463 (0.444-0.481) | 0.178 | 0.772 | 0.703 (0.608-0.798) | 0.528 | +0.244 | +0.209 | 73.2 |
| Reward gen 0, without temperature | 0.318 (0.302-0.334) | 0.072 | 0.722 | 0.648 (0.536-0.740) | 0.300 | +0.421 | +0.403 | 18.8 |
| NEWS2 OR gen 0, without temperature | 0.518 (0.501-0.533) | 0.207 | 0.835 | 0.758 (0.667-0.843) | 0.600 | +0.235 | +0.187 | 50.5 |
| Reward final (gen 1), without temperature | 0.131 (0.119-0.143) | 0.021 | 0.608 | 0.549 (0.441-0.648) | 0.091 | +0.516 | +0.528 | 20.2 |
| NEWS2 OR final, without temperature | 0.451 (0.431-0.471) | 0.166 | 0.759 | 0.692 (0.596-0.785) | 0.504 | +0.256 | +0.233 | 56.5 |

Chance rates and excess have no interval. The full output, with per-kind rows, is `held_out.txt`.

What this shows:
- Added to NEWS2, the final policy with temperature alerts in 7 more of 91 episode windows (64
  against 57) at 1.12 times NEWS2's alerts. Most of that is what the extra alerts give by chance:
  chance rises from 0.473 to 0.528, and the excess over chance is +0.244 against +0.210 for NEWS2
  alone. Excess has no interval. No gain beyond chance is shown.
- Without temperature: 6 more of 91 (63 against 57) at 1.09 times the alerts; chance 0.504
  against 0.473, excess +0.256 against +0.210. No gain beyond chance is shown.
- NEWS2 OR X can only add windows to NEWS2. The separate intervals (0.703 (0.608-0.798), 0.692
  (0.596-0.785), NEWS2 0.626 (0.522-0.727)) do not test that paired gain.
- Learning made generation 0 quieter, beyond noise: alerts 0.429 (0.414-0.443) to 0.153
  (0.140-0.165) with temperature, 0.318 (0.302-0.334) to 0.131 (0.119-0.143) without. Any-bin over
  all episodes fell, 0.725 to 0.571 and 0.648 to 0.549, with overlapping intervals.
- With temperature the final policy alerts late: median lead -3.8 min (generation 0: +23.0).
  Without temperature it is +20.2 (generation 0: +18.8).

What it cannot show:
- Anything about patients. These are simulated wards.
- Validation numbers are optimistic. Validation picks the shipped generation and judges the
  rounds, so it has been looked at many times.
- Held-out was read once for these policies. That is by procedure, not enforced by code. The same
  held-out seeds were used earlier for earlier policies of this card.
- Any-bin counts alerting up to 2 h after fully in. A quiet rule with a negative or near-zero lead
  time is not early warning.

## Limits

- Circular: learned and judged on the same simulator. An upper bound, not a performance claim.
- Synthetic, not validated.
- Few events: 4.47 evaluated episodes per trial in the pilot, so the per-generation tests have little
  power for small gains (see the power note).
- Excess is capped at 1 minus chance. Compare excess only between rows with similar chance.
- Weights and theta share a common scale: all can be multiplied by one factor without changing
  what fires. The search does not use that.
- Not wired to the API, the rank or the arbiter. NEWS2 is never changed.

## Huawei note

The policy is six numbers and runs as plain arithmetic on the baseline z values, so it needs no
model file or runtime. The loop is one script with a data folder in (`--data`) and a model folder
out (`--out`), so it could run as a ModelArts training job. That has not been done. Nothing was
run in the cloud.

## Reproduce

From the repo root, in either research venv (see `research/README.md`), data at `/data`:

    python -m research.reward_loop --data /data/trials/ward-v2-ext --out models/tier_b/reward_policy --round 1 [--jobs N]
    python -m research.reward_loop --data /data/trials/ward-v2-ext --out models/tier_b/reward_policy --round 2 --change selection_trials=16 --why "In round-1 gen-01, gen-03 and gen-04 the selection winner led the current policy on the 8 selection trials (+1.32, +0.21, +0.38) and its lead vanished on the 12 confirmation trials (mean d +0.32, -0.01, -0.33). This is the winner's-curse pattern; step size, a dead vital, the form and theta drift are not clearly supported by these records. 16 selection trials halve the selection variance so the winner sent to confirmation is more often truly better." [--jobs N]
    python -m research.reward_loop --out models/tier_b/reward_policy --stop "no change proposed: in round-2 gen-01 to gen-03 the winners were confirmed slightly better than the incumbent (mean d +0.20, +0.33, +0.12) but below their thresholds, which points to a plateau whose gains are near the 0.19 validation margin rather than to any menu key, as the round-2 proposal pre-registered."
    python -m research.reward_loop --data /data/trials/ward-v2-ext --out models/tier_b/reward_policy --no-temperature [--jobs N]
    python -m research.reward_loop --evaluate /data/trials/ward-v2 --out models/tier_b/reward_policy   # once, already done

Exit codes: 0 done, 2 refused (one line on stderr), 3 seeds missing (`needs seeds a to b`). A
round command resumes from its files; run again after its stop, it changes nothing. The
held-out command refuses a `policy.json` whose content sha256 does not match, or that is not the
ledger's incumbent (file sha256 `21cf038e4ab699906eeab87d56ddbd39965915a8ec085e75f56ca99501114775`).

Checked by a second agent that did not build it: every validation reward, confirmation test, seed
list, hash chain and ledger field was recomputed and matched. A from-scratch re-run of round 1,
round 2, the stop line and the no-temperature run wrote 20 byte-identical files. Before any run,
33 single-line mutations of the loop were tried; after the fixes each one fails a test (one
equivalent mutant aside).

## References

- Beyer, H.-G. (2000). Evolutionary algorithms in noisy environments: theoretical issues and
  guidelines for practice. Computer Methods in Applied Mechanics and Engineering 186, 239-267.
  https://doi.org/10.1016/S0045-7825(99)00386-2
- Jin, Y., Branke, J. (2005). Evolutionary optimization in uncertain environments - a survey.
  IEEE Transactions on Evolutionary Computation 9(3), 303-317.
  https://doi.org/10.1109/TEVC.2005.846356
- Smith, J. E., Winkler, R. L. (2006). The optimizer's curse: skepticism and postdecision surprise
  in decision analysis. Management Science 52(3), 311-322. https://doi.org/10.1287/mnsc.1050.0451
- Dwork, C., Feldman, V., Hardt, M., Pitassi, T., Reingold, O., Roth, A. (2015). The reusable
  holdout: preserving validity in adaptive data analysis. Science 349(6248), 636-638.
  https://doi.org/10.1126/science.aaa9375
