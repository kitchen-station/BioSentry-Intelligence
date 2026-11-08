# BioSentry

A ranked early-warning list for hospital wards in the Philippines.

BioSentry answers one question: *of your twenty patients, see this one first.* It takes
vital signs, adds what the hospital already knows (diagnosis, labs, and two values a nurse
types in: blood pressure and level of consciousness) and puts every patient on one ranked
list, one screen per clinician.

**Why.** Ward nurse-to-patient ratios run 1:20 or worse (DOH standard 1:12), and automated
alerts alone have not cut mortality: the gap is unreliable alerts and too few people to act.
BioSentry's contribution is the process, not the hardware: an offline-first ward system (patch,
gateway and edge server, cloud sync when the network allows) that cuts false alarms and tells
each nurse and physician who to see next.

The list is sorted by **NEWS2**, a published bedside early-warning score (Tier A). A patient
who meets a NEWS2 trigger sits above one who does not. Tier B (mechanism findings) is demo
content, not part of the scoring.

**This repository is frontend-only for now.** The backend, the ML models and the training code
were removed ahead of the Huawei overhaul; the dashboard runs on a committed synthetic ward
(`frontend/src/data/synthetic.json`). The earlier backend is in git history.

**This is not a diagnosis system.** It ranks and displays. A human decides.

## Run it

You need Node.js `^20.19` or `>=22.12`, and make. From the repository root:

```bash
make web-install
make web-dev
```

Open the URL Vite prints, choose a department and a clinician, and sign in with any
passphrase. Whatever you enter is kept in memory and is gone on reload.

`make check` runs lint, types and tests. `frontend/src/types/generated.ts` is the API
contract, frozen until the new backend exists. Every target is in the [Makefile](Makefile).

## What you will see

- **Sign-in:** pick a department and a clinician profile. This build accepts any passphrase.
- **Ward:** patients in priority order, each with its score and why. A patient the system
  cannot score is listed as NOT RANKED, with the reason and what to record to fix it.
- **Patient detail:** the score and what drives it, vitals and labs with where each value
  came from, notes, and trend charts.
- **Station:** admit a patient, enter lab results, or upload a document (extraction is a
  placeholder).

## Status

**Built**, on synthetic data:

- The dashboard: ward list, patient detail and station views, on a fixture provider.
- An HTTP provider that talks to an API with the same contract (no API in the repo yet).

**Pending:** a backend (scoring, clinician scope and audit), Tier B in the scoring, a signal-quality model, conformal abstention, distilled
edge models, anything measured on real patients, a sensor pipeline (IoT Device Access), a
database, real authentication, document extraction, and a plain-language explanation of each
ranked alert for the nurse or physician.

**Next:** deploy offline-first on Huawei Cloud (IoT Device Access, ModelArts, MindSpore Lite,
Object Storage Service), then compare against a threshold-alarm baseline on false alarms per
patient per shift. Not doing: federated learning, digital twins, reinforcement-learning
assignment, ECG foundation models.

This repository makes no claim about clinical performance. The scores are a published rule
applied to made-up patients.

## Rules the system keeps

- **I1** Missing is missing: nothing is imputed or defaulted to zero.
- **I2** Every value carries where it came from: source, tier, time, quality.
- **I3** Tiers stay separate: Tier A sorts.
- **I4** Abstention is an output: NOT RANKED is a row with a reason and an instruction.
- **I5** No training on BioSentry patients: local data calibrates and validates only.
- **I6** The synthetic flag propagates: every fixture record says `synthetic: true`.
- **I7** Privacy (RA 10173): one screen per clinician, their patients only, audit on every view.
- **I8** Transport: BLE to a gateway, then Wi-Fi or 4G to the cloud. NB-IoT is excluded.

## Design rules

- **Fuse inputs, never outputs.** A model may read raw and derived features; it never reads
  another tier's verdict.
- **Escalation arbiter**, the only component that changes displayed priority:
  1. Tier A threshold met: escalate unconditionally.
  2. Tier B significant finding: raise and attach the pathway.
  3. Notes-layer concern: raise and attach the trigger phrase (v2).
  4. Missing required input: NOT RANKED, naming what is missing.

  The fixture data follows rules 1 and 4.
- **Ward view** must be filtered server-side to the signed-in clinician's patients, on every
  call. The fixture provider stands in for that filter.
- **Look:** clean clinical instrument with depth. Two brand colours (navy primary, sand
  secondary) and two state colours: red means act (NEWS2 7+ solid, 5-6 or a single 3 as a red
  edge), green means stable or a good signal. Everything else stays neutral, so colour only
  marks what needs attention. Cards with a soft shadow; Source Serif 4 for clinical values,
  Inter for chrome. Tokens are in `frontend/src/index.css`.
- **Role-aware:** each row shows the NEWS2 response for the signed-in role (RCP 2017, Chart 4):
  a nurse sees whom to call, a physician sees what assessment is due.

## Before you expose it anywhere

- **Synthetic data only.** No real patient data exists in this repository.
- **Identity is a stub.** Sign-in accepts any passphrase. There is no real authentication.

## Licence

Competition entry for the Huawei ICT Competition Innovation Track. No real patient data exists in this repository.
