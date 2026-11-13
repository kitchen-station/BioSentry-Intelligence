# Simulator trials ward-v2-ext (data card)

| | |
|---|---|
| what | synthetic ward trials, trial format version 1 (`docs/trial-format.md` in the sim repo) |
| where | outside the repo, not committed: `~/Desktop/Projects/biosentry-data/trials/ward-v2-ext/seed-NN`, each with its own manifest |
| synthetic | yes, `synthetic: true` in every manifest. No real patient data |
| validated | no |

Seeds present when the models were trained: (filled in by the orchestrator)

## How it was made

- Simulator: `biosentry-ward-sim` origin/main at commit **b4b7cdc**. Node 25.
- Build: `npx vite build --ssr src/trial.ts --outDir dist/trial`
- Command per seed:
  `TRIAL_SIM_COMMIT=b4b7cdc node dist/trial/trial.js --scenario scenarios/default.json --seed N --days 30 --out seed-NN`
- 30 simulated days each, default scenario (ward W4), scenario sha256 `95052d02...98a8`
  (`95052d021b73da3fa71cc62601a795264b8ffd1cf5c4bf2e6e3ba407f74698a8`).

## Same generator as ward-v2

Seed 31 made at b4b7cdc was compared with `ward-v2-train/seed-31` (made at f7647c5). `cohort.csv`,
`episodes.csv` and `summary.json` were byte-identical. The manifest was identical too when the
recorded commit string is the same (the two folders record different commit strings). So these
trials are more draws from the ward-v2 generator, not a new one. The loaders still refuse a trial
whose days, speed or scenario sha256 differ from ward-v2's (30, 60, the hash above).

## Roles by seed range

| Seeds | Role |
|---|---|
| 61-160 | distilled-student training (slice M1) |
| 161-260 | not used; mostly not generated; the student loader refuses them |
| 261-300 | distilled-student validation (early stopping, model choice, threshold) |
| 301-340 | conformal calibration (M3); the student loader opens only `manifest.json` and skips them |
| 341-400 | reward-loop validation |
| 401 and up | reward-loop generations (the reward-loop card gives the exact range used) |

Never used for a reported held-out number: those come from ward-v2 seeds 11-30, scored once.
Ward-v2 seeds 1-10 are no longer used by the student.

## Limits

- Circular: the episodes, the vitals and the judging all come from one simulator, the same one
  that makes ward-v2.
- `outcome` (higher care) is decided by NEWS2 inside the sim and is never an event or a label.
- Different seeds are different draws, not independent simulators: scenario, case mix and
  generator are shared with ward-v2.
