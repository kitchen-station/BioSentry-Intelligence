# Change from own baseline (Tier B)

| | |
|---|---|
| id | `tier_b/baseline` |
| tier | B (unsupervised per patient; no labels, no learned weights, no artifact) |
| code | `backend/src/biosentry/domain/scoring/tier_b/baseline.py` |
| status | implemented, synthetic, not validated; not wired to the API or the arbiter |
| intended use | research, display only. It never ranks, never alerts and never looks at NEWS2 |

## What it is

For each device vital, it learns what is normal for this patient from their first hours on the
ward, then says when the latest readings have stayed away from that in the clinically bad
direction. It is plain arithmetic (median and median absolute deviation): there is nothing to
train and no ONNX export is needed.

## Inputs

One vital's 15-minute values since admission, oldest first, `None` for a gap. Vitals: heart
rate (bpm), respiratory rate (/min), SpO2 (%), temperature (°C). Integers are readings. A
non-finite value (NaN, infinity) or a bool is treated as a gap. An unknown vital is refused. Gaps are skipped, never filled, and are never evidence either way.

## Outputs

A `Finding` (id `baseline/<vital>`, direction, `significant`, pathway: circulatory for heart
rate, respiratory for respiratory rate and SpO2, temperature for temperature) with a `Deviation`
beside it: latest value, baseline median, deviation in the vital's own units and as z, how many
consecutive bins it has held and the bin where that run started. Or `NotComputed`:
"baseline forming, N of 24 bins", or "latest bin missing".

## How the baseline is formed

- Window: the first 24 valid bins (6 h of data) after admission, then frozen. Anchored, not
  rolling, so a slow 24-48 h drift is never absorbed into "normal".
- Spread: 1.4826 × MAD of the window, never below the vital's floor.
- z = (latest - median) / max(spread, floor).
- Significant when z is at least k in the bad direction (heart rate up, respiratory rate up,
  SpO2 down, temperature up) for p consecutive valid bins ending at the latest bin. A gap ends
  the run.
- The run may reach back into the window itself (24 bins ending in 3 high ones is significant,
  starting at bin 21): a deviation inside the first six hours is still a deviation from the
  patient's own start.

## Parameters (judgement until the B4 sweep tunes them)

| Parameter | Default | Why |
|---|---|---|
| window | 24 valid bins | 6 h; a 24 h window (as in the ward study below) would leave the riskiest first day without a baseline |
| k | k = 2.0 | The worked example's smallest full deviation is about 2.5 wander SDs (heart rate). A spread from 24 bins by MAD has roughly 25% relative error, so 2.5 / 1.25 = 2.0 still catches it |
| p | p = 3 | 45 min: longer than one noisy bin, short against ramps of 30 to 2,580 min. It costs at least 30 min of lead time (two bins before the third); a value hovering near k costs more, since one bin just under k resets the run |
| floors | 2 bpm, 1 /min, 1 %, 0.1 °C | Stops a very stable patient alarming on noise |

All four are judgement, marked `shortcut:` in code, until the offline sweep (slice B4) tunes
them on synthetic cohorts. A tuned value replaces the default here and in code together.
Parameters are validated (window and p at least 1, k and every floor positive and finite) and
read-only once made.

Ward study: Amsterdam UMC surgical ward (authors not checked here). *Adaptive threshold-based
alarm strategies for continuous vital signs monitoring.* J Clin Monit Comput, 2021. Its
personalised thresholds came from each patient's first 24 h.

Worked example (research note, section 1): a respiratory episode on a patient at heart rate 70,
respiratory rate 16, SpO2 97 ends near 85.5, 23 and 92.5. NEWS2 scores that 4 with no single 3,
so it does not trigger; with the baseline spread at the fitted wander SD the z values are about
2.5, 2.7 and 2.8, all past k = 2.0.

## Sweep result on ward-v2 (temperature rise removed, noise 0.1 C; synthetic, not validated)

ward-v2 is ward-v1 made again by the fixed simulator (sim commit f7647c5, after PR #25 removed
`INJECT_TEMPERATURE` and raised temperature noise from 0.02 to 0.1 C). Same seeds, scenario, days and command. Same grid, rule, windows, chance
control and bootstrap. Only the data changed. Data card: `models/data/ward-v2/card.md`.

- Tuning seeds 1 to 10 (56 episodes, 51 not cut short, 3,072 control windows). NEWS2 chart
  trigger alone: 0.405 alerts per patient per 12 h, any-bin 39/51, excess +0.327.
- Chosen: **k = 4.0, p = 8** (was k = 3.0, p = 4 on ward-v1), the corner of the grid. Of the 19
  settings within the alert budget, 17 catch exactly 41 of 51 for "NEWS2 OR baseline" and two
  catch 42. The pick was decided by control windows, not by episodes.
- Held-out seeds 11 to 30, run once after the choice (91 episodes, 79 not cut short, 4,678
  chance windows). Any-bin = alerting in any bin, episodes not cut short. "All" = all 91
  evaluated episodes, with the patient-level 95% interval. Chance and excess have no interval.

| Policy | Alerts / patient / 12 h (95% CI) | Any-bin | Any-bin, all (95% CI) | Chance | Excess | New alert, hypotensive | Lead |
|---|---|---|---|---|---|---|---|
| NEWS2 chart trigger alone | 0.414 (0.395-0.434) | 0.684 (54/79) | 0.626 (0.522-0.727) | 0.473 | +0.210 | 0.074 (2/27) | 47.2 |
| Baseline alone | 0.042 (0.035-0.049) | 0.278 (22/79) | 0.264 (0.174-0.360) | 0.032 | +0.246 | 0.037 (1/27) | -82.5 |
| NEWS2 OR baseline | 0.426 (0.407-0.444) | 0.722 (57/79) | 0.659 (0.558-0.758) | 0.486 | +0.235 | 0.111 (3/27) | 39.6 |
| Baseline alone, no temperature | 0.032 (0.026-0.038) | 0.266 (21/79) | 0.253 (0.165-0.343) | 0.025 | +0.241 | 0.000 (0/27) | -85.3 |
| NEWS2 OR baseline, no temperature | 0.425 (0.405-0.442) | 0.709 (56/79) | 0.648 (0.550-0.745) | 0.484 | +0.225 | 0.074 (2/27) | 44.6 |

What this shows:
- The baseline adds nothing measurable to NEWS2. "NEWS2 OR baseline" catches 3 more of 79
  episodes, and its interval over all episodes (0.558-0.758) overlaps NEWS2's (0.522-0.727).
- Baseline alone is now a quiet, late rule: about a tenth of NEWS2's alerts, 22 of 79 episodes,
  median 82 minutes after fully in. Its excess (+0.246) is above NEWS2's (+0.210), but excess is
  capped at 1 minus chance (0.968 against 0.527 here), so the two cannot be compared.
- With and without temperature now give almost the same rows. The gains seen on ward-v1 went away on ward-v2. Two things changed (the temperature rise was removed and temperature noise went from 0.02 to 0.1 C), and this run cannot say which one caused it.
- Across all 30 ward-v2 trials the median temperature change from the 2 h before an episode to
  the hour after it is fully in is -0.06 C (tachycardic), +0.06 C (hypotensive), +0.05 C
  (respiratory). On ward-v1 the same measure gave +0.52, +0.61 and +0.61 C.

What it cannot show: anything about patients. It is still one simulator judged on itself, with
79 episodes. It does not say that no baseline rule could help: only this rule, with this grid
and this selection rule, on this simulator.

## Earlier result on ward-v1 (kept for comparison; the simulator then added a fixed temperature rise)

- Swept: k in 1.5, 2.0, 2.5, 3.0, 3.5, 4.0 and p in 2, 3, 4, 6, 8 (window 24 and the floors stay
  as they are), on tuning seeds 1 to 10 of ward-v1 only (56 episodes, 51 not cut short).
- Rule, fixed before the run: among settings where "NEWS2 chart trigger OR baseline" raises at
  most 25% more alerts per patient per 12 h than the chart trigger alone, take the largest excess
  over chance of alerting in any bin on episodes not cut short; ties go to fewer alerts, then
  larger k, then larger p.
- Chosen then: k = 3.0, p = 4 (`params.json` now holds the ward-v2 choice and grid; the ward-v1
  file is in git history before this change). The detector's
  DEFAULTS in code (k = 2.0, p = 3) are NOT changed by this slice; only the research command
  reads `params.json`. On the tuning seeds k = 2.0, p = 3 is over the alert budget.
- Held-out seeds 11 to 30, run once after the choice (91 episodes, 79 not cut short, 4,678
  chance windows). Any-bin alerting on episodes not cut short, with chance and excess:

| Policy | Alerts per patient per 12 h | Any-bin | Chance | Excess |
|---|---|---|---|---|
| NEWS2 chart trigger alone | 0.413 | 0.709 (56/79) | 0.472 | +0.237 |
| Baseline alone | 0.189 | 0.848 (67/79) | 0.178 | +0.670 |
| NEWS2 OR baseline | 0.473 | 0.924 (73/79) | 0.550 | +0.374 |

- Sensitivity check, not a tuned policy: the same k and p with temperature left out of the
  baseline (the `*_no_temp` rows of `research.evaluate`):

| Policy | Alerts per patient per 12 h | Any-bin | Chance | Excess | New-alert, hypotensive |
|---|---|---|---|---|---|
| Baseline alone | 0.189 | 0.848 | 0.178 | +0.670 | 0.630 (17/27) |
| Baseline alone, no temperature | 0.139 | 0.557 (44/79) | 0.137 | +0.420 | 0.000 (0/27) |
| NEWS2 OR baseline | 0.473 | 0.924 | 0.550 | +0.374 | 0.444 (12/27) |
| NEWS2 OR baseline, no temperature | 0.462 | 0.747 (59/79) | 0.533 | +0.213 | 0.111 (3/27) |

- Without temperature, NEWS2 OR baseline has an excess of +0.213, below NEWS2 alone at +0.237: so
  added to NEWS2, the baseline without temperature gives no excess over NEWS2 alone (the excess
  has no interval). Baseline alone without temperature still has any-bin 0.557, chance 0.137, excess +0.420, 0.139 alerts per
  patient per 12 h and catches 0 of 27 hypotensive episodes as new alerts. Excess is not
  comparable across policies with different chance rates.
- Cause: the ward simulator adds a fixed temperature rise in every episode kind
  (`INJECT_TEMPERATURE = 0.6` in the sim's `src/sim/source.ts`, marked `shortcut:` there because
  too few eICU episodes chart temperature) and uses a fixed temperature noise of 0.02 (also a
  `shortcut:`). Both are uncited simulator values, so any detector that reads temperature is
  flattered until they have a source.
- The hypotensive gain is a simulator artefact. Across all 30 trials, the median change from the
  2 h before an episode starts to the hour after it is fully in is: hypotensive episodes, heart
  rate -1.84 bpm and temperature +0.56 C (34 of 37 above +0.3 C); respiratory, +0.59 C (55 of
  62); tachycardic, +0.51 C (18 of 23). Every simulated episode kind raises temperature by about
  half a degree, and with the 0.1 C floor and k = 3 a 0.3 C shift flags. Without temperature,
  baseline alone catches 0 of 27 hypotensive episodes as new alerts. The rise from 0.111 to 0.444
  is not evidence that the detector sees low blood pressure. It is circular.
- Lead time: the baseline is late. Median lead time is -18.8 min alone and +20.5 min for the OR
  policy, against +45.9 min for NEWS2 alone. "Any-bin" counts alerting up to 2 h after the
  episode is fully in, so 0.848 does not mean early.

## Limits of the sweep

- Circular: tuned and judged on the same simulator that made the data.
- Cohort rows hold whole-number values at the end of each 15-minute bin; the API uses 15-minute
  medians. The detector sees a coarser signal here than in the product.
- ward-v1: the winner was decided by 9 of 3,066 control windows, not by episodes: k = 3.0, p = 4
  and k = 2.5, p = 6 both catch 46 of 51. ward-v2: 17 of the 19 settings within budget catch 41
  of 51, so again the control windows decided.
- Excess is capped at 1 minus chance, so it cannot be compared across policies with different
  chance rates. ward-v1: the OR policy could reach at most +0.45, baseline alone at most +0.82.
  ward-v2: at most +0.51 and +0.97.
- "Held-out run once" is by procedure; nothing in the code enforces it.
- 51 tuning episodes: neighbouring settings differ by a few episodes, so the exact winner is not
  stable. Read the grid, not only the pick.
- `validated: false`. No claim about real patients.

## Limits

- Circadian swing (heart rate amplitude 6.9 bpm in the simulator) is only partly inside a 6 h
  window, so time of day can look like deviation; the sweep picks k with that in it.
- No baseline for cuff blood pressure or consciousness (intermittent nurse entries). On ward-v1,
  hypotensive episodes were caught only through the simulator's temperature rise; on ward-v2
  baseline alone catches 1 of 27 as a new alert.
- A deterioration that starts inside the six-hour window widens the spread and moves the median,
  which lowers sensitivity.
- Re-anchoring after a patch is re-applied is not implemented; the caller would have to slice
  the input to start at patch-on.
- A patient who arrives already deteriorating gets a wrong baseline: their abnormal is learned
  as their normal.
- The simulator creates deterioration as an additive offset on top of a stable anchor, exactly
  the shape this method looks for, so results on simulator data are an upper bound. Tested on
  the simulator that generated the data, it is circular.
- Synthetic, not validated. No claim about performance on real patients.
