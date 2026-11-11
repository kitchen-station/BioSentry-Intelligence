# research: offline evaluation of alerting rules (synthetic data only)

Standard-library Python plus an import of the backend's own `biosentry` package. No dependency, no
`pyproject.toml`, nothing here is served by the API. This slice holds the yardstick: how NEWS2
alone performs as an alerting rule against the simulator's true deterioration episodes. Every
later model is compared with these numbers.

- `trials.py` reads one trial folder (format version 1: `manifest.json`, `cohort.csv`,
  `episodes.csv`, `summary.json`). It refuses an unknown format version, a missing file, a wrong
  header, a manifest without `synthetic: true`, and rows that break the contract. It opens only
  those four names, never follows a symlinked file, never evaluates content, and caps what it
  reads: 64 KiB per JSON file, 64 MiB per CSV, 1,000 characters per line, 1,000,000 cohort rows,
  100,000 episode rows.
- `rescore.py` re-scores every cohort row with the backend's NEWS2 and requires it to equal the
  sim's `ranked` and `news2` (a mismatch stops the run and names trial, patient and minute).
- `alerts.py` holds the policies and the alert rule; `events.py` the episode windows;
  `evaluate.py` the metrics and the command.

## Run

Trial data lives outside the repository. The real trials for this slice are
`~/Desktop/Projects/biosentry-data/trials/ward-v1/seed-01` to `seed-30` (30 simulated days each).

    python -m research.evaluate <trial folder>...

It prints plain deterministic text (no clock, no paths) and exits 2 with one line on any refusal.
Trials must share format version, days, speed and scenario hash.

## The split

Seeds 1 to 10 are "tuning", 11 to 30 "held out". Metrics use the held-out seeds only. Tuning seeds
are loaded and parity-checked, then not scored; nothing is tuned in this slice. A seed outside
1 to 30 is refused.

## Definitions

- Event: the generator's true deterioration episode. It starts at `episode_start`, is fully in at
  `episode_start + ramp_minutes`, and its window runs from the start to fully-in + 120 minutes.
  Lead time is fully-in minus the FIRST new alert in the window (positive: before fully in).
- Three detection measures per policy, printed side by side and not interchangeable:
  (a) NEW alert: an alert fires inside the window. (b) ALERTING in any bin: the policy is alerting
  in at least one bin of the window. (c) the count with (b) but not (a): the alert was already open
  before the window. (a) is not monotone in the policy: a policy that alerts earlier and more can
  score fewer new alerts, because an alert opened before the window stays open and cannot fire again.
- Dropped: episodes of the 12 starting patients (admit minute 0; true arrival unknown, and one
  has a scripted worsening that is not an episode), episodes starting after the trial, and
  episodes starting outside the patient's stay. Starting patients stay in the alert-burden counts.
  Kept episodes whose window is cut short by the end of the stay or trial are counted; sensitivity
  is printed for all evaluated episodes and for episodes not cut short, and the episodes whose stay
  or trial ended before they were fully in are counted separately. A kept episode with no cohort
  rows at all is counted on its own line and is not detected.
- Chance control (no random draws): each evaluated episode's window (same offset from admission,
  same length) is laid on every admitted, non-starting patient of the same trial whose stay covers
  the whole window and in whose stay no episode begins (no episode, or one starting after the stay
  or trial ended). The shares of control windows with a new alert and with an alerting bin are the
  chance rates; "excess over chance" is sensitivity minus that. Control windows are never cut
  short, so the like-for-like comparison is the "not cut short" sensitivity. The chance rates have
  no interval.
- Alert: one open alert per patient; it fires when the policy turns from not alerting to
  alerting and re-arms after 16 bins (4 hours) not alerting (a judgement, the same for every
  policy). A missing bin or a NOT RANKED row is not alerting.
- Burden: alerts per patient per 12-hour shift and per 100 patient-days (patient-days from each
  `summary.json`), and time in alert (cohort rows alerting / cohort rows).
- Abstention: NOT RANKED rows over cohort rows; bins with no row over the bins expected in the
  stays (every 15-minute boundary from admission to just before leaving); and the two together.
  The episodes whose start fell in a NOT RANKED or missing bin (the bin ending at or after the start)
  are counted.
- Policies: `news2_chart_trigger` is the product's arbiter itself (`arbitrate(...).escalated`:
  total 5 or more, or any single parameter at 3); `news2_total_ge_5` is total 5 or more only.
  Add a policy by adding a `Policy` to `alerts.POLICIES`.
- Intervals: patient-level bootstrap (each patient of the held-out trials, with replacement), 1,000
  resamples, a SplitMix64 generator (`bootstrap.py`, fixed seed 20261112, no `random` module),
  2.5th to 97.5th percentile, for the alert rate and for the two sensitivities. Exposure per
  patient is the stay inside the trial.

## Limits

- Circular. The data and the episodes come from the same simulator that the alerts are judged
  on, so this says how a rule behaves on this simulator, not on patients. A model trained on
  simulator trials and then scored on simulator trials is circular in the same way. Output ends
  with "synthetic, not validated".
- "Higher care" (`outcome`) is decided by NEWS2 inside the sim, so it is never an event.
- Read sensitivity next to its chance rate. With alerts this frequent, a window laid on a patient
  with no episode often contains a new alert or an alerting bin by itself, so only the excess over
  chance can be attributed to the episode.
- Hypotensive episodes are visible only once a nurse takes a cuff pressure. The output prints how
  many hypotensive windows have no cuff reading carried by a bin inside them (NEWS2 saw no new
  blood pressure); the low
  hypotensive sensitivity comes from that and from the new-alert rule (an alert already open at the
  window start does not count), not from one cause alone.
- The toy cohort in `tests/data/toy` has gaps on purpose (a few dozen rows, hand-computed
  answers, `toy_expected.txt` is the exact expected output) and a hand-set `patient_days`.

## Gate (RESEARCH-GATE)

    docker run --rm -v <repo>:/app -v ~/Desktop/Projects/biosentry-data:/data:ro -v biosentry-test-venv:/venv -w /app -e PYTHONPATH=/app/backend/src:/app -e PYTHONDONTWRITEBYTECODE=1 biosentry-api-build sh -c "/venv/bin/python -m pytest -q -p no:cacheprovider research/tests; echo pytest-exit \$?"

It must print "N passed" and `pytest-exit 0`. If `/venv/bin/python` is missing, run
`uv sync --locked -q` in `/app/backend` first with `-e UV_PROJECT_ENVIRONMENT=/venv -e
UV_PYTHON_DOWNLOADS=never`. The backend's coverage gate does not measure `research/`.
