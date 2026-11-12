# Reward-driven alerting policy (Tier B, research only)

| | |
|---|---|
| id | `tier_b/reward_policy` (and `tier_b/reward_policy/no_temperature`) |
| files | `policy.json`, `generations/gen-00.json` to `gen-10.json`; the same under `no_temperature/` |
| code | `research/reward_policy.py` (policy, reward), `research/reward_loop.py` (loop, held-out run) |
| status | learned on synthetic trials, not validated. Not wired to the API, the rank or the arbiter |
| intended use | research. Tier A (NEWS2) is never changed or learned |

## Result on ward-v2 (temperature rise removed, noise 0.1 C)

The committed files are now learned on ward-v2 and ward-v2-train: the same trials made again by
the fixed simulator (sim commit f7647c5, after PR #25 removed `INJECT_TEMPERATURE` and raised
temperature noise from 0.02 to 0.1 C). Same seeds, scenario, days and command; data card `models/data/ward-v2/card.md`. Same loop, reward,
lambda, seeds and harness. Nothing was tuned again. The B4 rows use the ward-v2 sweep choice
(k = 4.0, p = 8); generation 0 is still fixed in code at k = 3.0, p = 4 (weights 1, theta 3).

- Shipped, with temperature: generation 4 (validation reward 0.688). Weights: heart rate 0.35,
  respiratory rate 0.78, SpO2 1.20, temperature 0.08. Theta 2.29, p 6. On ward-v1 the shipped
  policy put its largest weight on temperature (0.70); here temperature is the smallest.
- Shipped, no temperature: generation 9 again, with the same six numbers as on ward-v1 (only the
  validation numbers moved, so its sha256 changed).
- A candidate was again accepted in all 20 generations. Generation 0 with temperature was
  rejected on validation again. Validation rests on 9 episodes, so the pick is a selection on
  noise, as before.
- Held-out seeds 11 to 30, scored once (91 episodes, 79 not cut short, 4,678 chance windows):

| Policy | Alerts/pt/12 h (95% CI) | Time in alert | Any-bin | Any-bin, all (95% CI) | Chance | Excess | New-alert excess | Lead |
|---|---|---|---|---|---|---|---|---|
| NEWS2 chart trigger | 0.414 (0.395-0.434) | 0.158 | 0.684 | 0.626 (0.522-0.727) | 0.473 | +0.210 | +0.179 | 47.2 |
| B4 baseline alone (k 4.0, p 8) | 0.042 (0.035-0.049) | 0.012 | 0.278 | 0.264 (0.174-0.360) | 0.032 | +0.246 | +0.253 | -82.5 |
| NEWS2 OR B4 | 0.426 (0.407-0.444) | 0.165 | 0.722 | 0.659 (0.558-0.758) | 0.486 | +0.235 | +0.210 | 39.6 |
| Reward gen 0 | 0.429 (0.414-0.443) | 0.123 | 0.785 | 0.725 (0.625-0.816) | 0.435 | +0.350 | +0.301 | 23.0 |
| NEWS2 OR gen 0 | 0.535 (0.520-0.550) | 0.248 | 0.861 | 0.802 (0.716-0.885) | 0.666 | +0.195 | +0.084 | 67.9 |
| Reward final (gen 4) | 0.150 (0.138-0.162) | 0.029 | 0.646 | 0.582 (0.464-0.678) | 0.120 | +0.525 | +0.534 | -11.2 |
| NEWS2 OR final | 0.465 (0.446-0.482) | 0.173 | 0.772 | 0.703 (0.608-0.798) | 0.516 | +0.256 | +0.240 | 61.5 |
| Reward gen 0, no temperature | 0.318 (0.302-0.334) | 0.072 | 0.722 | 0.648 (0.536-0.740) | 0.300 | +0.421 | +0.403 | 18.8 |
| NEWS2 OR gen 0, no temperature | 0.518 (0.501-0.533) | 0.207 | 0.835 | 0.758 (0.667-0.843) | 0.600 | +0.235 | +0.187 | 50.5 |
| Reward final (gen 9), no temperature | 0.061 (0.052-0.068) | 0.017 | 0.519 | 0.473 (0.370-0.576) | 0.050 | +0.469 | +0.478 | -38.7 |
| NEWS2 OR final, no temperature | 0.434 (0.414-0.452) | 0.166 | 0.722 | 0.659 (0.560-0.753) | 0.492 | +0.229 | +0.204 | 60.9 |

What this shows:
- Added to NEWS2, the final policy gives no gain beyond noise: any-bin over all episodes 0.703
  (0.608-0.798) against NEWS2's 0.626 (0.522-0.727), intervals overlapping, for 12% more alerts.
- Alone, the final policy catches about as many episodes as NEWS2 (0.582 against 0.626,
  intervals overlapping) at about a third of the alerts (0.150 against 0.414, intervals apart),
  but later: median lead -11.2 min against +47.2. Its excess (+0.525) is above NEWS2's
  (+0.210), but excess is capped at 1 minus chance (0.880 against 0.527), so the two are not
  comparable. On new alerts it fires in more windows than NEWS2 (0.560 against 0.363,
  intervals just overlapping), but mostly after the episode is fully in. This is the same
  quiet, late pattern as the no-temperature policy on ward-v1.
- On ward-v1 the final policy alone had any-bin 0.769 (0.676-0.852) and excess +0.700, and
  NEWS2 OR final had 0.857 (0.779-0.922). The gains seen on ward-v1 went away on ward-v2. Two things changed (the temperature rise was removed and temperature noise went from 0.02 to 0.1 C), and this run cannot say which one caused it.

What it cannot show: anything about patients, or that a fewer-alerts rule is safe. "Any-bin"
counts alerting up to 2 h after fully in, so similar any-bin with a negative lead time is not
early warning.

## Earlier result on ward-v1 (kept for comparison; the simulator then added a fixed temperature rise)

Everything from here to "Huawei note" describes the ward-v1 run (sim commit 3a15e01). Its
`policy.json` and generation files are in git history before this change.

## Summary (ward-v1)

- It did not beat the untrained B4 rule.
- With temperature, it ended within noise of B4.
- Without temperature, added to NEWS2 it gives nothing: excess +0.243 against +0.237 for NEWS2
  alone, with no interval.
- The path from generation to generation is mostly noise. A candidate was accepted in all 20
  generations. Each generation trains on about 15 episodes, and validation has 9.
- Synthetic, not validated. Circular: learned and judged on the same simulator.

## What it is

Six numbers. For each 15-minute bin, each device vital's distance from the patient's own
baseline in the bad direction (z from the backend's `baseline_finding`, clipped at 0) is
multiplied by a weight and summed. The policy alerts when that sum is at least theta in p
bins in a row. A missing value or a forming baseline adds nothing. A NOT RANKED bin is not
scored and ends a run. Each bin reads only itself and earlier bins.

Generation 0 is the B4 rule (k = 3.0, p = 4) in this form: weights 1, 1, 1, 1, theta 3, p 4.
A sum cannot say "any one vital past 3", so generation 0 alerts whenever B4 does on RANKED
bins, and also when several vitals are a little off together. It never alerts on a NOT RANKED
bin, which B4 can.

## Why this is learning by reward, and what it is not

The policy is changed only by a reward computed from simulator runs. No label is fitted. Each
generation starts from the last one and is tried on three trials no earlier generation saw.

It is NOT deep reinforcement learning. There is no state-action value, no per-step credit, no
gradient. It is a small black-box search (a (1+30) evolution step) with a reward: draw 30
nearby settings, keep the best if it beats the current one on the same trials.

## Data

Synthetic only, sim commit 3a15e01, 30 simulated days per trial.

- Training: `ward-v1-train` seeds 31 to 60, three per generation, each used once.
- Validation and the stop: `ward-v1` seeds 8 to 10 (9 evaluated episodes in all).
- Held-out: `ward-v1` seeds 11 to 30, scored once at the end.
- Seeds 1 to 7 are not used. The loaders read `manifest.json` first and refuse seeds 11 to 30
  before any other file is opened (tested).

## Reward (fixed before any run)

Per trial: C - 0.5 x E x (a / a_NEWS2).

- C: evaluated episodes with the policy alerting in any bin from episode start to fully in
  (caught early).
- E: evaluated episodes on the trial. a: the policy's alerts per patient per 12 h. a_NEWS2: the
  NEWS2 chart trigger's on the same trial. So both terms are counted in episodes.
- lambda = 0.5. A policy with NEWS2's alert count has to catch more than half the episodes
  early to beat a silent one. This is judgement and was never tuned.
- Hard rule: if the time in alert is above the NEWS2 chart trigger's on any trial of the set,
  the policy is rejected.
- `outcome` (higher care) is never part of the reward.

## Loop

30 candidates per generation, drawn by a SplitMix64 generator with seed 20261112 + g. The
draw adds normal noise to the current numbers: sigma 0.5 for the weights and theta, 1.0 for p.
Sigma is multiplied by 0.9 after each generation. A candidate is accepted only if its mean
reward is strictly above the current policy's on that generation's trials. After every
generation, the current policy is scored on seeds 8 to 10. The shipped policy is the
generation with the best validation reward. The no-temperature run is the same, with the
temperature weight fixed at exactly 0.

A second full run of the loop with temperature wrote byte-identical files. The no-temperature
loop was not rerun in full. A test on a tiny synthetic cohort checks that both modes give the
same bytes twice. Each loop takes about 3 minutes.

## Learning curve

Train = the mean reward over that generation's three new seeds: first the policy it started
from, then the best candidate. "rej" = candidates rejected for time in alert. Val = seeds 8
to 10. "rejected" = above NEWS2's time in alert on a validation seed.

| gen | seeds | with temperature: train before -> best (rej) | val | no temperature: train before -> best (rej) | val |
|---|---|---|---|---|---|
| 0 | - | - | rejected | - | 0.373 |
| 1 | 31-33 | rejected -> 0.820 (19) | 0.575 | 0.270 -> 0.941 (7) | 1.042 |
| 2 | 34-36 | 0.502 -> 1.635 (2) | -0.007 | 1.257 -> 1.579 (0) | 0.512 |
| 3 | 37-39 | 0.991 -> 1.664 (1) | 0.252 | 0.369 -> 1.352 (0) | 0.966 |
| 4 | 40-42 | 0.617 -> 0.888 (0) | 0.151 | 0.259 -> 0.567 (0) | 0.596 |
| 5 | 43-45 | 1.694 -> 1.729 (1) | -0.016 | 0.860 -> 1.392 (0) | 0.400 |
| 6 | 46-48 | 0.014 -> 0.617 (10) | 0.417 | 0.625 -> 0.928 (0) | 0.252 |
| 7 | 49-51 | 0.706 -> 1.253 (0) | 0.349 | 0.592 -> 1.113 (0) | 0.467 |
| 8 | 52-54 | 1.132 -> 1.452 (1) | 0.580 | 0.734 -> 0.840 (0) | 0.205 |
| 9 | 55-57 | 2.742 -> 2.984 (0) | 0.439 | 2.157 -> 2.586 (0) | **1.073** |
| 10 | 58-60 | 0.495 -> 1.004 (0) | **0.921** | 0.811 -> 0.931 (0) | 0.538 |

- Shipped, with temperature: generation 10. Weights: heart rate 0.18, respiratory rate 0,
  SpO2 0.58, temperature 0.70. Theta 3.23, p 1.
- Shipped, no temperature: generation 9. Weights: heart rate 0.44, respiratory rate 1.13,
  SpO2 0.35. Theta 3.24, p 6.
- Generation 0 with temperature was rejected on validation. Its time in alert was above NEWS2's
  on seeds 9 and 10 (0.121 against 0.117; 0.143 against 0.100).

Read this curve as noise. A candidate was accepted in every generation, in both runs. With 30
draws scored on only about 15 episodes, the best draw nearly always beats the current policy
on those same trials. The validation reward does not rise steadily, and it rests on 9 episodes.
Picking the best of 11 validation scores is itself a selection on noise.

Leaving out any one validation seed picks the same generation. The shipped generation catches
no more validation episodes early than earlier generations. With temperature it catches 5 of 9,
as generations 0, 7 and 8 did, so the pick is the fewest alerts at equal catches. Without
temperature it catches 4 of 9, fewer than generation 0's 5. It wins by raising fewer alerts.

## Held-out numbers (seeds 11 to 30, scored once)

91 episodes, 79 not cut short, 4,678 chance windows, 7,566 patient-days.

- Any-bin: alerting in any bin of the window, episodes not cut short. CI is the 95% interval
  over all evaluated episodes (the harness has none for the not-cut-short rate).
- Excess: any-bin minus chance.
- New-alert excess: new alert minus its own chance, episodes not cut short.
- Lead: median minutes before fully in, episodes with a new alert.

| Policy | Alerts/pt/12 h (95% CI) | Time in alert | Any-bin | Any-bin, all (95% CI) | Chance | Excess | New-alert excess | Lead |
|---|---|---|---|---|---|---|---|---|
| NEWS2 chart trigger | 0.413 (0.394-0.432) | 0.159 | 0.709 | 0.648 (0.545-0.745) | 0.472 | +0.237 | +0.194 | 45.9 |
| B4 baseline alone | 0.189 (0.175-0.203) | 0.047 | 0.848 | 0.769 (0.677-0.851) | 0.178 | +0.670 | +0.653 | -18.8 |
| NEWS2 OR B4 | 0.473 (0.455-0.490) | 0.189 | 0.924 | 0.846 (0.771-0.913) | 0.550 | +0.374 | +0.306 | 20.5 |
| Reward gen 0 | 0.428 (0.413-0.442) | 0.127 | 0.886 | 0.813 (0.727-0.886) | 0.436 | +0.450 | +0.353 | 23.0 |
| NEWS2 OR gen 0 | 0.533 (0.518-0.548) | 0.251 | 0.937 | 0.868 (0.790-0.933) | 0.665 | +0.271 | +0.139 | 56.5 |
| Reward final (gen 10) | 0.179 (0.165-0.194) | 0.033 | 0.835 | 0.769 (0.676-0.852) | 0.135 | +0.700 | +0.676 | 19.3 |
| NEWS2 OR final | 0.462 (0.444-0.479) | 0.177 | 0.937 | 0.857 (0.779-0.922) | 0.526 | +0.410 | +0.341 | 33.8 |
| B4 alone, no temperature | 0.139 (0.126-0.151) | 0.035 | 0.557 | 0.505 (0.400-0.600) | 0.137 | +0.420 | +0.444 | -14.1 |
| NEWS2 OR B4, no temperature | 0.462 (0.444-0.480) | 0.181 | 0.747 | 0.681 (0.583-0.770) | 0.533 | +0.213 | +0.199 | 59.7 |
| Reward gen 0, no temperature | 0.318 (0.302-0.334) | 0.072 | 0.722 | 0.648 (0.536-0.740) | 0.300 | +0.421 | +0.403 | 18.8 |
| NEWS2 OR gen 0, no temperature | 0.517 (0.500-0.533) | 0.207 | 0.848 | 0.769 (0.674-0.851) | 0.600 | +0.248 | +0.188 | 50.5 |
| Reward final (gen 9), no temperature | 0.061 (0.052-0.068) | 0.017 | 0.519 | 0.473 (0.370-0.576) | 0.050 | +0.469 | +0.478 | -38.7 |
| NEWS2 OR final, no temperature | 0.433 (0.412-0.451) | 0.166 | 0.734 | 0.670 (0.570-0.763) | 0.491 | +0.243 | +0.206 | 58.1 |

Chance rates and excess have no interval. The full output, with per-kind rows, comes from the
reproduce command below.

## Did learning by reward improve on generation 0?

- With temperature: it cut alerts beyond noise. They went from 0.428 to 0.179 per patient per
  12 h, and the intervals do not overlap. Generation 0's sum form alerts more than twice as
  often as the B4 rule (0.428 against 0.189 per patient per 12 h), so most of this fall undoes
  the translation rather than improving on B4. Time in alert went from 0.127 to 0.033. Detection
  did not change beyond noise: any-bin over all episodes 0.813 against 0.769, with overlapping
  intervals. But the loop only found its way back to about the B4 rule. B4 alone gives 0.189
  alerts, any-bin 0.848 and excess +0.670; the final policy gives 0.179, 0.835 and +0.700.
  These numbers are within noise of each other. Learning beat generation 0's sum form, not the
  untrained B4 rule.
- Without temperature: it learned to be quiet and late. Alerts fell from 0.318 to 0.061 (no
  interval overlap). Any-bin fell from 0.648 to 0.473; the intervals overlap, so that fall is
  not beyond noise either. Median lead time went from +18.8 to -38.7 min. Excess went from
  +0.421 to +0.469, but its cap moves when chance falls (see the limits). This is not a better
  detector. Added to NEWS2, it gives excess +0.243 against +0.237 for NEWS2 alone, with no
  interval. No gain.

## What depends on temperature

The final policy with temperature puts its largest weight on temperature (0.70) and alerts on
one bin (p = 1). With the 0.1 °C floor, the simulator's uncited fixed rise of about 0.6 °C in
every episode is a z of about 6 by itself. So every row with temperature is flattered,
including the B4 rows and the generation 0 row. Without temperature, no learned row adds
anything measurable to NEWS2.

## Limits

- Circular: learned and judged on the same simulator. An upper bound, not a performance claim.
- Synthetic, not validated. No claim about real patients.
- Temperature artefact: see above.
- Few events. About 15 episodes per generation and 9 for validation, so the training and
  validation rewards are mostly noise.
- Excess is capped at 1 minus chance. Rows with low chance (the quiet policies) can reach a
  higher excess, so compare excess only between rows with similar chance.
- Weights and theta can all be scaled by the same factor without changing the policy. The
  search does not use that.
- "Held-out scored once" is by procedure. The code does not stop a second run.
- Not wired to the API, the rank or the arbiter. NEWS2 is never changed.

## Huawei note

The policy is 6 numbers and runs as plain arithmetic on the baseline z values, so it needs no
model file or runtime. A ModelArts training job would be this same script, with a data folder
in (`--train`, `--validate`) and a model folder out (`--out`). That has not been done here.

## Reproduce

Run from the repo root, in either research venv (see `research/README.md`), with the data
mounted at `/data`:

    python -m research.reward_loop --train /data/trials/ward-v2-train --validate /data/trials/ward-v2 --out models/tier_b/reward_policy
    python -m research.reward_loop --train /data/trials/ward-v2-train --validate /data/trials/ward-v2 --out models/tier_b/reward_policy --no-temperature
    python -m research.reward_loop --evaluate /data/trials/ward-v2 --out models/tier_b/reward_policy

The held-out command refuses a `policy.json` whose content does not match the sha256 stored in
it (ward-v2). With temperature: `e2a74b78ad544d33ec3a59b13f0513754273ec83aac2ab9109875d61e8d991ac`.
No temperature: `2dea2cd388efdbdb312d29f4486517c5633a21d9709afc4f69a6297d41df5a79`. (ward-v1: `cb8eb5d9...` and `2a20d944...`.)

That sha256 is of the policy's content (the file without its own `sha256` field), so `shasum` on the
file itself gives a different value.