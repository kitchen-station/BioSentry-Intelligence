# Simulator trials ward-v2 and ward-v2-train (data card)

| | |
|---|---|
| what | synthetic ward trials, trial format version 1 (`docs/trial-format.md` in the sim repo) |
| where | outside the repo, not committed: `~/Desktop/Projects/biosentry-data/trials/ward-v2/seed-01..30` and `ward-v2-train/seed-31..60`, each with a `SOURCE.md` |
| synthetic | yes, `synthetic: true` in every manifest. No real patient data |
| validated | no |

## How it was made

- Simulator: `biosentry-ward-sim` origin/main at commit **f7647c5** (it contains PR #25, ae80706,
  which removed the fixed temperature rise `INJECT_TEMPERATURE = 0.6` and raised the temperature
  noise from 0.02 to 0.1 C, a marked judgement). Golden hash in
  `src/sim/golden.test.ts`: 8749072d. Node 25. The one commit after ae80706 changes only drawing
  code (`src/render/`, `App.tsx`, `panels.tsx`, `perf.ts`), not the trial runner.
- Build: `npx vite build --ssr src/trial.ts --outDir dist/trial --logLevel warn`
- Command per seed:
  `TRIAL_SIM_COMMIT=f7647c5 node dist/trial/trial.js --scenario scenarios/default.json --seed N --days 30 --out seed-NN`
- ward-v2: seeds 1 to 30 (1 to 10 tuning, 11 to 30 held out). ward-v2-train: seeds 31 to 60
  (reward-policy training only). 30 simulated days each, default scenario (ward W4, scenario
  sha256 95052d02...).

## Against ward-v1

ward-v1 and ward-v1-train were made by the same command at sim commit 3a15e01. Only the
simulator version differs. Both sets have 2,436 admissions and 164 episodes over seeds 1 to 30;
the episodes are the same, the vitals are not. Median temperature change from the 2 h before an
episode to the hour after it is fully in (all 30 seeds, non-starting patients):

| Episode kind | ward-v1 | ward-v2 |
|---|---|---|
| tachycardic (23) | +0.52 C | -0.06 C |
| hypotensive (37) | +0.61 C | +0.06 C |
| respiratory (62) | +0.61 C | +0.05 C |

ward-v1 is kept, unchanged, for comparison.

## Limits

- Circular: the episodes, the vitals and the judging all come from one simulator.
- `outcome` (higher care) is decided by NEWS2 inside the sim and is never an event or a label.
- The temperature noise (0.1 C) is a marked judgement in the sim (`src/sim/source.ts`), not a
  fitted value. Episode shapes come from the sim's fit; see the sim README for sources.
- Both changes of PR #25 (no rise, more noise) differ between ward-v1 and ward-v2, so a
  difference between the two runs cannot be split between them.
