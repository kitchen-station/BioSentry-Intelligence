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

## Sweep result (slice B4, synthetic, not validated)

- Swept: k in 1.5, 2.0, 2.5, 3.0, 3.5, 4.0 and p in 2, 3, 4, 6, 8 (window 24 and the floors stay
  as they are), on tuning seeds 1 to 10 of ward-v1 only (56 episodes, 51 not cut short).
- Rule, fixed before the run: among settings where "NEWS2 chart trigger OR baseline" raises at
  most 25% more alerts per patient per 12 h than the chart trigger alone, take the largest excess
  over chance of alerting in any bin on episodes not cut short; ties go to fewer alerts, then
  larger k, then larger p.
- Chosen: **k = 3.0, p = 4** (in `params.json`, which also holds the whole grid). The detector's
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

- Without temperature, NEWS2 OR baseline has an excess of +0.213, below NEWS2 alone at +0.237: on
  this simulator the baseline adds nothing measurable beyond the temperature artefact. Baseline
  alone without temperature has any-bin 0.557, chance 0.137, excess +0.420, 0.139 alerts per
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
- The winner was decided by 9 of 3,066 control windows, not by episodes: k = 3.0, p = 4 and
  k = 2.5, p = 6 both catch 46 of 51.
- Excess is capped at 1 minus chance, so it cannot be compared across policies with different
  chance rates: the OR policy can reach at most +0.45, baseline alone at most +0.82.
- "Held-out run once" is by procedure; nothing in the code enforces it.
- 51 tuning episodes: neighbouring settings differ by a few episodes, so the exact winner is not
  stable. Read the grid, not only the pick.
- `validated: false`. No claim about real patients.

## Limits

- Circadian swing (heart rate amplitude 6.9 bpm in the simulator) is only partly inside a 6 h
  window, so time of day can look like deviation; the sweep picks k with that in it.
- No baseline for cuff blood pressure or consciousness (intermittent nurse entries). Hypotensive
  episodes are caught here only through the simulator's temperature rise (see the sweep result).
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
