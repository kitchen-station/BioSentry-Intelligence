# Simulator trials ward-v2-heldout2 (data card)

| | |
|---|---|
| what | synthetic ward trials, trial format version 1 (`docs/trial-format.md` in the sim repo) |
| where | outside the repo, not committed: `~/Desktop/Projects/biosentry-data/trials/ward-v2-heldout2/seed-NNNN` (container: `/data/trials/ward-v2-heldout2`), each with its own manifest |
| synthetic | yes, `synthetic: true` in every manifest. No real patient data |
| validated | no |
| licence | none stated: our own simulator output |
| present when read | (filled in by the orchestrator) |

Seeds 1001 to 1400: 400 trials of 30 simulated days. This is "held-out set 2". Ward-v2 seeds 11 to 30
are "held-out set 1" and are spent.

## How it was made

- Simulator: `biosentry-ward-sim` main at commit **4b86f96**.
- Command per seed:
  `TRIAL_SIM_COMMIT=4b86f96 node dist/trial/trial.js --scenario scenarios/default.json --seed N --days 30 --out seed-N`
- 30 simulated days each, default scenario (ward W4), scenario sha256 `95052d02...98a8`
  (`95052d021b73da3fa71cc62601a795264b8ffd1cf5c4bf2e6e3ba407f74698a8`).

## Same generator as ward-v2

Seed 31 made at 4b86f96 was byte-identical to `ward-v2-train/seed-31`: all four files, with the commit
string equal. So these trials are more draws from the ward-v2 generator.

## Use

- Held-out set 2 only. Each evaluation command reads it once (see "Held-out set 2" in `research/README.md`).
- Never used to train, tune, calibrate or run the reward loop. Seeds 1001 to 1400 are the same trials in any
  folder, so every such loader refuses them by seed number.
- Nothing had read these trials when the analysis was declared (2026-11-14).

## Limits

- Circular: the episodes, the vitals and the judging all come from the simulator that made ward-v2.
- Different seeds are different draws, not independent simulators: scenario, case mix and generator are shared.
- A gain shown on this set is a statement about this simulator only.
