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

## Limits

- Circadian swing (heart rate amplitude 6.9 bpm in the simulator) is only partly inside a 6 h
  window, so time of day can look like deviation; the sweep picks k with that in it.
- No baseline for cuff blood pressure or consciousness (intermittent nurse entries), so
  hypotensive episodes are Tier A only.
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
