# Tier B ectopy head: premature ventricular beat classifier (M3 exit, PVC row)

| | |
|---|---|
| id | `tier_b/ectopy` |
| tier | B (mechanism finding) |
| version | 0.1.0-spike |
| status | **research**: trained for study; not for ranking, not wired, not for product use |
| artifact | `weights.npz` (boosted trees as arrays, band-pass coefficients, parity vectors); sha256 pinned in `models/registry.yaml` |
| pin | `pin.json` freezes the method, thresholds, records, code hashes and minimums before the test split is scored |
| use | evaluation and development only |
| claims | retrospective, public data. For the test split: "inter-patient test split of MIT-BIH Arrhythmia (DS2), same database as the development split", never "external" or "validated" (P10) |

## What it does

Calls each beat that a plain numpy R-peak detector finds in independent 10 s windows of a
single-lead ECG as a premature ventricular beat (annotated `V`) or not. A beat gets twelve numbers:
the intervals before and after it relative to the window's median interval (with presence flags),
how it differs from the window's own median beat (correlation, amplitude and width ratios,
polarity), and its absolute width and amplitude. Sixty boosted trees of depth 3 turn them into one
probability; at 0.40 or above the beat is called. It displays; a human decides. It does not rank
(Tier B never ranks, I3) and it is not a diagnosis.

## Interface

- Input: a 10 s window of single-lead ECG at 250 Hz (2,500 samples), contiguous and complete. A
  window with a missing sample is not passed in (I1): nothing is filled or interpolated.
- Preprocessing (per window, so windows stay independent): causal Butterworth band-pass
  0.5-40 Hz from zero state, then median/MAD normalisation clipped at +/-10: the same recipe as
  the rhythm head.
- A beat is **callable** when its snippet (0.24 s either side of the R peak) lies inside its
  window. A beat that is not callable is never called, and a `V` among them is a false negative.
  A beat at a window's edge has no interval on one side; the model is told so by a presence flag.
- Output: a probability per callable beat. Size: 60 trees, 862 nodes, `weights.npz` 29 KB.
- No ONNX Runtime, scikit-learn or scipy at inference: the trees are arrays walked with numpy.

## Training data and licences (public; no BioSentry patient data: I5)

Read on the PhysioNet project page and its licence page on 2026-10-14: "Open Data Commons
Attribution License v1.0" and "Anyone can access the files, as long as they conform to the terms
of the specified license."; the licence page is ODC-By v1.0, whose rights "explicitly include
commercial use" (section 3.1). Same corpus and same terms as the rhythm head's card.

| Corpus | Use here | Version, DOI | Licence page |
|---|---|---|---|
| MIT-BIH Arrhythmia Database (48 half-hour 360 Hz recordings, 47 subjects; lead MLII by name, resampled to 250 Hz) | development: the 22 DS1 records (training and record-grouped cross-validation). Test: the 21 records of DS2 less 202, scored once after the pin. Out: the paced records 102, 104, 107, 217 (paced beats annotated in each) and 202 (same subject and tape as 201, which is a development record) | 1.0.0 (2005-02-24), https://doi.org/10.13026/C2F305 | https://physionet.org/content/mitdb/view-license/1.0.0/ |

Attribution (required): Moody GB, Mark RG, "The impact of the MIT-BIH Arrhythmia Database", IEEE
Eng in Med and Biol 2001;20(3):45-50; Pollard T et al., "PhysioNet as a global platform for
biomedical research", Nature Health 2026, DOI 10.1038/s44360-026-00096-z.

The model was never trained, tuned or calibrated on BioSentry patients (I5). Local data, when it
exists, may only calibrate and validate.

**Disclosure.** The annotation counts of the DS2 records (3,220 `V` in 16 of 22 records, 202
included) were read once on 2026-10-14 to establish that a PVC metric was feasible. No DS2 signal,
detector output or method result has been computed on DS2 at the time of this card.

## Development results (DS1; record-grouped five-fold cross-validation; not a test result)

Beat level, 150 ms one-to-one matching, annotated `V` the only positive; every other beat symbol,
`F` (fusion) and `E` (escape) included, is a negative and stays in the denominators. A detection
called PVC counts against PPV matched or not; specificity is over annotated non-`V` beats. DS1
holds 3,677 `V` beats in 22 records.

| Method | Sensitivity | PPV | Specificity | F1 |
|---|---|---|---|---|
| Learned head, out-of-fold, threshold 0.40 | 0.701 | 0.708 | 0.982 | 0.704 |
| Primary comparator: RR-ratio rule (interval before / window median < 0.89) | 0.610 | 0.573 | 0.966 | 0.591 |
| Secondary comparator: ratio < 0.95 and interval after > 1.10 x median | 0.478 | 0.720 | 0.986 | 0.575 |

The head's row is out-of-fold: each record was scored by a model that never saw it. The two rules
have two parameters each, chosen on DS1 by pooled F1 (their rows are resubstitution on DS1, which
for two parameters is a mild optimism). The threshold is the first maximum of pooled out-of-fold
F1 over 0.05 to 0.95. Boosted-tree settings were fixed before any fold was fitted and early
stopping is off (its validation split would be a beat-level random one). Folds are record-grouped
and dealt out by `V` count: {115, 122, 124, 205, 208}, {106, 112, 114, 207, 220},
{101, 109, 116, 223}, {108, 119, 215, 230}, {118, 201, 203, 209}; 201 is in DS1, 202 is out.

- Detector (threshold fraction 0.15, chosen on DS1: most `V` beats found with beat PPV >= 0.99):
  finds 97.5% of `V` beats (92 not found, 29 of them only because of where the 10 s windows
  fell), against 85.9% at the AF baseline's 0.25. A further 173 detected `V` beats are not
  callable (snippet leaves the window). Together about 7% of `V` beats cannot be called.
- False positives of the head by annotated symbol: 579 `N`, 92 `R`, 71 `A`, 67 `F`, 31 `a`, 16
  `L`, 4 `Q`, 3 `E`, 1 `J`. The RR-ratio rule's: 1,075 `N`, 436 `A`.
- Hardest development records (out-of-fold): 223 (375 of 473 `V` called normal: multiform,
  bidirectional ventricular tachycardia), 208 (243 missed and 67 fusion beats called `V`), 106
  (171 missed, multiform), 203 (450 false positives: muscle noise, multiform), 207 (199 false
  positives: bundle branch blocks) and 118 (126 false positives: right bundle branch block).
- A raw-snippet 1-D CNN was tried first on the same folds and called bundle-branch-block beats
  PVC (out-of-fold F1 0.35; 0.51 with a window-template channel and batch norm). With 22
  development patients the beat-relative features generalised across records and the CNN did not.
  Neither CNN is shipped.

## The test, to be run once

Frozen in `pin.json` before the test split is touched: the method and its thresholds, the code
files by sha256, the record lists, the definitions above, and the minimums set by the Evaluator
before the counts were known: **pooled sensitivity >= 0.80 and PPV >= 0.80 as point estimates**,
with record-level 95% intervals (1,000 resamples, seed 20261014) quoted and not gating. Pass or
fail does not depend on beating the comparator; the paired difference with its interval is
reported, and this card will say plainly if the learned head adds nothing. Records with zero PVCs
are listed and stay in the pooled denominators; beats lost to where the windows fell are counted
next to sensitivity. From the pin on, the test split is consumed: no decision may be informed by
it, it is scored once, and a rerun is only a reproduction check with identical output.

No test-split number exists in this card yet. The development numbers above are a reason to
expect that 0.80 / 0.80 may not be met.

## Limitations (read before quoting any number)

- Single lead (MLII) at 250 Hz from one database, one recorder, one site and annotators. The test
  split is the same database as development: PVC across devices and sites is untested (a
  cross-database check on INCART or SVDB is recommended and needs the user's yes to download).
- Not validated on chest-patch, ward or Philippine data.
- 22 development patients: record-level variability dominates; a few V-rich records carry most
  `V` beats, so pooled numbers hang on them.
- Multiform PVCs, ventricular tachycardia episodes, bundle branch blocks and noisy records are
  where it fails; fusion (`F`) and escape (`E`) beats count as negatives.
- Windows are independent 10 s: the first beat of a window has no interval before it, the last
  none after it, and a beat within 0.24 s of an edge is not callable.
- **The M3 exit gate is not met by this card.** Outstanding at least: the test-split run and its
  verdict, MindSpore Lite parity; the Evaluator's gate decides, and nothing here claims it.

## Reproduce

`training/rr_baseline.py` (detector), `training/pvc.py` (`develop`: detector fraction and
comparator parameters on DS1) and `training/pvc_head.py` (`train`: folds, threshold, weights;
Python 3.11, numpy 2.4.6, scipy 1.17.1, scikit-learn 1.9.1, wfdb 4.3.1). Data are fetched at run
time by `training/fetch_data.py` (checksum-verified) and never committed.
