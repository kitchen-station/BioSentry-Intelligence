# Dataset card: eICU-CRD Demo 2.0.1

Written 2026-11-12, before any analysis of this dataset. Numbers from our own runs are added at the
bottom afterwards and are aggregates only.

## What it is
The demo subset of the eICU Collaborative Research Database (Philips eICU telehealth programme),
hosted by PhysioNet: https://physionet.org/content/eicu-crd-demo/2.0.1/ (DOI 10.13026/4mxk-na84).
De-identified electronic records of intensive-care unit stays. Open access: no account, no data-use
agreement. Licence: Open Data Commons Open Database License v1.0 (ODbL).

## Who is in it
- ICU patients (all ages, we use adults) in US hospitals, unit admissions in 2014 and 2015.
- 2,520 unit stays, 1,841 distinct patients, 186 hospital ids (counted from `patient.csv.gz`). Mostly
  mixed medical-surgical ICUs, plus cardiac, neuro, cardiothoracic and surgical units.
- Hospital ids are anonymised numbers. Dates are offsets in minutes from unit admission.
- Sampling by the publisher is not described as random for us to rely on; treat it as a convenience
  sample of ICU stays. It is not a ward population.

## What we use (tables)
`patient`, `vitalPeriodic` (heart rate, respiration, SaO2; 5-minute monitor values; arterial
pressure), `vitalAperiodic` (non-invasive systolic), `nurseCharting` (temperature, GCS total, O2 device),
`respiratoryCharting` (O2 device), `infusiondrug` (vasopressors), `carePlanGeneral` (comfort and
no-CPR entries). Nothing else is read.

## What is missing for NEWS2, and how each input is handled
Missing is missing. A missing input is never filled with a default, an average or an interpolated
value (README invariant I1). A bin with a missing input is not given a full NEWS2: the backend scorer
abstains (NOT RANKED, the project's abstention rule), and a second, clearly separate figure adds up
only the components that were measured (a lower bound, marked partial). The share of bins with all
inputs is always printed next to any NEWS2 number.

| NEWS2 input | source | handling |
|---|---|---|
| heart rate, respiratory rate, SpO2 | `vitalPeriodic`, 5-minute values | median of the in-range values in the 15-minute bin; none in the bin: missing |
| SpO2 scale | none recorded | scale 1 for every patient. Hypercapnic patients (scale 2) are scored on the wrong scale; a known limit |
| systolic BP | non-invasive cuff or arterial line, whichever is latest | latest value within 60 minutes, its time kept; older: missing |
| temperature | nurse charting (C) and monitor probe | only values from 30 to 45 C are kept; anything outside (for example a Fahrenheit value in the Celsius column) is dropped and counted, not converted by guesswork; latest kept value within 4 hours; older: missing |
| consciousness | GCS total | 15 is "A"; 3 to 14 is "not A" (scores 3, as any non-A does). "Unable to score due to medication" and no entry within 4 hours: missing. Sedated and intubated patients therefore score "not A": a limit of this mapping |
| supplemental oxygen | O2 device text from nurse and respiratory charting | room air is "no", a known device (cannula, mask, ventilator, CPAP and so on) is "yes", unclear text ("other") or no entry within 4 hours: missing. Flow and FiO2 are not used |

## Labels
No ward-transfer label exists. The event is the first of: first vasopressor infusion at 6 hours or
later after unit admission, or death in the unit. Vasopressor start is a treatment decision, not
physiology, and its time is the charting time (`infusionoffset`), so treatment can predate it by minutes. Stays with a vasopressor before 6 hours are left out. Deaths with "Comfort measures only" or
"No CPR" charted more than 12 hours before death are left out (comfort-care deaths are not
unexpected deterioration). Details and counts: `research/README.md`, "Real data".

## Limits
- ICU, not ward. Monitored every 5 minutes, one nurse for one or two patients, treated in place. A
  vasopressor start in an ICU is not an ICU transfer from a ward.
- US hospitals, 2014 to 2015. Philippine public wards differ in case mix, staffing and devices.
- Small. The held-out test set is about 400 stays (aggregates in the run output), so intervals are wide.
- Test set: the 46 hospitals the generator fit (fit-generator.mjs) did not use; 38 of them were used by the circadian calibration fit (fit-eicu.mjs), so this is mostly, not fully, a different-site test. Hospital ids are in `research/realdata/split.py`.
- Charted temperature, GCS and oxygen device are sparse; NEWS2 often runs on stale values or abstains.

## Licence terms and what we must do
ODbL 1.0. We must say where content came from when we publicly use a Produced Work (a chart, a
number, model weights), and any Derivative Database we publicly use must stay under ODbL
(share-alike). We commit no data and no row-derived file (our privacy rule; any published derivative database would be ODbL share-alike). Only aggregate numbers appear in the repo.
Full quotes and the checksum table: `~/Desktop/Projects/biosentry-data/eicu-crd-demo/2.0.1/SOURCE.md`.

Attribution line to use: "Contains information from the eICU Collaborative Research Database Demo
2.0.1 (PhysioNet), made available under the Open Database License (ODbL) v1.0."

## Wording the team may use
"tested on eICU-CRD Demo, an open ICU dataset; not validated for ward use"

Do not say validated, clinically validated, or proven for wards.

## Our run (2026-11-12, aggregates only; test hospitals only)
Command and definitions: `research/README.md`, "Real data". 632 stays in the 46 test hospitals; 375 patients kept
(one stay each, adult, first stay, 12 hours or more, no vasopressor before hour 6); 1,065 patient-days; 24 events
(14 vasopressor, 10 unit death); 102,428 fifteen-minute bins. Full NEWS2 inputs in 5.5% of bins (oxygen device
present in 14.9%, GCS in 40.7%, temperature 68.8%, systolic BP 83.5%, SpO2 82.6%, respiratory rate 80.3%, heart rate
92.3%). The numbers for NEWS2 and the baseline rule are in the command's output, not repeated here.

Note (ward-v2): the eICU results were produced with k = 3.0, p = 4, the ward-v1 sweep choice. `research/realdata/evaluate.py` reads `models/tier_b/baseline/params.json`, which now holds the ward-v2 choice (k = 4.0, p = 8), so a re-run would use k = 4.0, p = 8 and would not reproduce the published baseline numbers. It has not been re-run.

Out-of-range values dropped (not converted): SpO2 1,112, systolic 62, temperature 55, heart rate and respiratory
rate under 5 each. Not-alert consciousness (sedated, ventilated patients) is present in 18,748 of the 43,052 bins
where the "measured-components sum (NOT NEWS2; lower bound)" fires and alone causes the firing in 10,767: in an ICU close
to an outcome-linked input. Baseline rule limits: its parameters (k = 3.0, p = 4) were tuned on simulated data, and its baseline is
the first 6 ICU hours, often the sickest; for events at 6 to 12 hours the baseline period overlaps the event window.
Excess cannot be compared between rules with different chance rates; as a share of ceiling the order changes, and the intervals overlap.

With 24 events and full NEWS2 inputs in 5.5% of bins, this run cannot rank NEWS2, the measured-components sum or the baseline rule against each other; no conclusion can be drawn.

