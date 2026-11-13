# training: how each model was trained, its run log, notebook and registry

`research/` holds the code. `training/` explains and logs: this file, the run logs, the notebooks and the
registry. `models/` holds the small shipped files and their cards. `training/agent/` belongs to another session
and is not described here. Everything is synthetic and not validated.

## What is trained

| Model | One command (inside the research environment) | Data | Result goes to |
|---|---|---|---|
| Distilled vitals student (M1), two MindSpore networks per variant | `python -m research.train_student <folders> --run_log training/runs/student_vitals/<run id>.json --run_id <run id> --commit <sha>` | ward-v2-ext seeds 61-160 train, 261-300 validate | `models/tier_c/student_vitals/` |
| Reward-driven alerting policy (M2), six numbers | `python -m research.reward_loop --data <ward-v2-ext> --out models/tier_b/reward_policy --round 1` (then the rounds, `--stop`, `--no-temperature`) | ward-v2-ext seeds 341-400 validate, 401 and up generations | `models/tier_b/reward_policy/` |
| Confidence check (M3), one score quantile | `python -m research.confidence calibrate <folders> --run_log training/runs/confidence_check/<run id>.json --run_id <run id> --commit <sha>` | ward-v2-ext seeds 301-340 | `models/tier_c/confidence_check/` |
| Plain export of the student (M5) | `python -m research.export_plain` | the student's own files | `models/tier_c/student_vitals/model.json` |
| Baseline rule, k and p | `python -m research.sweep <folders> --write-params models/tier_b/baseline/params.json` | ward-v2 tuning seeds 1-10 | `models/tier_b/baseline/` |

The full commands, options and the rules fixed before each run are in `research/README.md`. Held-out seeds
(ward-v2 11-30) are scored once, at the end, and never by a notebook.

## Run logs

`training/runs/<model>/<run id>.json`. Each holds the settings, the seeds, the framework and package versions,
the loss per epoch (student) or the reward per generation (reward policy) or the calibration quantiles
(confidence check), the validation scores, the commit and a `time` object. Everything outside `time` is
deterministic. A run id is `m<slice>-<yyyymmdd>` (the student adds the short commit). Logs are small and have
no path.

- The student's log is written by `train_student --run_log`, the confidence check's by `confidence calibrate --run_log`.
- The reward policy's is written by `python -m research.reward_loop --run-log <file> --run-id <id> --commit <sha> --out <policy folder>`. It only reads the committed generation records, policy files and ledger, opens no trial, and has no wall time (its `time` object says so). `<sha>` is the commit that holds the final policy files.
- `--commit` is whatever the person running it passes: research code starts no process, so it cannot ask git. The hash is the branch commit when the run was made; after the squash merge, find the run by its run id and the pull request. The manifest and the registry name it `commit_at_run`.

## Notebooks

`training/notebooks/student_vitals.ipynb`, `reward_policy.ipynb`, `confidence_check.ipynb`. A notebook only
reads the newest run log of its model and the committed result files, and draws them. It never trains, never
opens a trial, never computes a new held-out number (it quotes the held-out text files written once). It uses
paths from the repository root, no clock and no random numbers. They are committed with their outputs.

Run them again with `python training/notebooks/execute.py [name ...]` in the research environment (needs
`nbclient`, `ipykernel`, `matplotlib`). `research/tests_ml/test_notebooks.py` runs them headless and fails on
an error, an absolute path, a read under a trials folder, or an import of MindSpore, scikit-learn,
onnxruntime or `research.student`.

## Registry

`training/REGISTRY.md` lists every shipped model file (from `models/manifest.json`, regenerated with
`python -m research.manifest --registry`), the datasets used (written by hand) and a section for the
language model that another session fills in.

## Running as a ModelArts job

The student's command accepts `--data_url` and `--train_url`, the argument names a Huawei ModelArts custom
training job uses: every sub-folder of `data_url` is a trial and `train_url` is the output folder. It runs on
CPU by default, makes no network call and names no local path. On ModelArts the trial folders would sit in an
OBS bucket, with a custom image holding the locked requirements and `libgomp1`. Nothing was run in the cloud,
no account was made and nothing was uploaded.
