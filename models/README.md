# models: the small shipped files, their cards and their manifest

Everything here is small and synthetic-trained, not validated. Trial data and any large file stay outside
the repository. The code that makes these files is in `research/`; how each one was trained, its run
log, notebook and registry row are in `training/`.

## Layout

| Folder | What it holds |
|---|---|
| `tier_a/news2/` | the NEWS2 card (the rule is code in `backend/`, there is no model file) |
| `tier_b/baseline/` | `params.json` (the baseline rule's k and p) and its card |
| `tier_b/reward_policy/` | `policy.json` (six numbers), `no_temperature/policy.json`, the card, `held_out.txt`, the ledger `experiments.jsonl`, and the generation records under `rounds/` and `no_temperature/generations/` |
| `tier_c/student_vitals/` | the distilled student: `model.onnx`, `model.mindir`, `model.ms` (hand-run Lite step), `model.json` (plain export), the `_no_temp` versions, `metadata.json`, `exports.json`, `timing.json`, `convert_lite.sh`, the card |
| `tier_c/confidence_check/` | `calibration.json` and `calibration_no_temp.json` (each with a `.sha256`), `heldout.txt` |
| `data/` | dataset cards only (no data) |
| `manifest.json` | one record per shipped model file: path, model, variant, framework and version, format, bytes, sha256, training seeds, run id, `commit_at_run` |

## The manifest and its rule

`manifest.json` is never edited by hand. `python -m research.manifest --write` makes it from the files and
their own metadata (standard library only); `python -m research.manifest --check` exits 2 with one line per
difference (a changed byte, a missing file, an unlisted model file, a changed field). A test runs the check in
the gates.

The rule for what counts as a shipped model file is fixed by folder and file name, in `research/manifest.py`:

- `tier_c/student_vitals/`: `model.onnx`, `model.mindir`, `model.ms`, `model.json` and the same four with `_no_temp`.
- `tier_b/reward_policy/`: `policy.json` and `no_temperature/policy.json`. The dropped round's policy under `rounds/` is a record.
- `tier_c/confidence_check/`: `calibration.json` and `calibration_no_temp.json`.
- `tier_b/baseline/`: `params.json`.

Everything else under `models/` must be a record: `*.md`, `*.txt`, `*.jsonl`, `*.sh`, `*.sha256`, the student's
`metadata.json`, `exports.json` and `timing.json`, and anything under `rounds/` or `generations/`. A file that is
neither is refused, so a new model file cannot be added without a line in the rule.

Where a record's fields come from (a field recorded nowhere is `null`, never guessed):

- Student: `framework`, `framework_version` and the seeds from `metadata.json`; the run id and commit from the newest run log under `training/runs/student_vitals/` that matches `metadata.json` (framework version, seeds, student sizes).
- Reward policy: the generation and validation seeds from the policy file; the run id and commit from the run log whose ledger lines name that file's sha256. Framework: "none (six numbers, plain arithmetic)".
- Confidence check: the calibration seeds from its JSON; the run id and commit from the run log that names its sha256.
- Baseline: its own `chosen_on`. It has no run log (`research.sweep` writes none), so run id and commit are `null`.
- The commit cannot come from git (research code starts no process). It is the `commit` the run log was given with `--commit`. When there is none, the record says why in `commit_note`. It is stored as `commit_at_run`. The hash is the branch commit when the run was made; after the squash merge, find the run by its run id and the pull request.
- Student files also carry `written_by`: `.mindir` MindSpore export, `.onnx` built with onnx.helper from the MindSpore weights, `.ms` MindSpore Lite converter, `.json` `research.export_plain`.

## Retrain, one command per model

Run inside the research environment (`research/README.md`, ML-GATE). Each command takes the trial folders from
outside the repository. Add `--run_log`, `--run_id` and `--commit` to keep a run log under `training/runs/`.

    python -m research.train_student <ward-v2-ext folders, seeds 61-160 and 261-340> --out models/tier_c/student_vitals --run_log training/runs/student_vitals/<run id>.json --run_id <run id> --commit <sha>
    python -m research.export_plain
    python -m research.confidence calibrate <ward-v2-ext folders, seeds 301-340> --run_log training/runs/confidence_check/<run id>.json --run_id <run id> --commit <sha>
    python -m research.reward_loop --data <ward-v2-ext> --out models/tier_b/reward_policy --round 1   # then rounds 2 to 4, --stop, --no-temperature (research/README.md)
    python -m research.reward_loop --run-log training/runs/reward_policy/<run id>.json --run-id <run id> --commit <sha> --out models/tier_b/reward_policy
    python -m research.sweep <ward-v2 folders> --write-params models/tier_b/baseline/params.json
    python -m research.manifest --write && python -m research.manifest --registry

## Hosting plan

Small models ship in this repository. When the stack is hosted, large files would go to a private Huawei Cloud OBS bucket that the private server reads; this is not built and nothing has been uploaded.
