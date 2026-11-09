# BioSentry

**Live demo:** <https://biosentry-dashboard.pages.dev> (synthetic data, no real sign-in; deploys from `main`)

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

**What is here.** The dashboard (`frontend/`) and a slim API (`backend/`: FastAPI over PostgreSQL). The
ML models and the training code were removed ahead of the Huawei overhaul and are in git history. The
API serves the committed synthetic ward (`frontend/src/data/synthetic.json`), seeded into the database,
and takes a live ward from the ward simulation.

**This is not a diagnosis system.** It ranks and displays. A human decides.

## Run it

You need Node.js `^20.19` or `>=22.12` and make. For the API you also need
[uv](https://docs.astral.sh/uv/) (it fetches Python 3.11 itself) and [Docker](https://docs.docker.com/get-docker/)
(the API's database is PostgreSQL, run from `compose.yaml`). From the repository root:

```bash
make web-install
make web-dev
```

Open the URL Vite prints, choose a department and a clinician, and sign in. With no API (this is the
public demo) there is no server and no real sign-in: any passphrase is accepted, the dashboard serves
the fixture from memory, and whatever you enter is gone on reload.

**With the API**, so that scores are computed by the real NEWS2 and arbiter, and everything you
record is kept:

```bash
make api-install
make db-up                         # PostgreSQL 18 in Docker on 127.0.0.1:5432, waits until healthy
make api-dev                       # http://127.0.0.1:8000, loopback only; creates the schema and seeds on start
cd frontend && VITE_API_URL=http://127.0.0.1:8000 npm run dev
```

Sign in with the dev passphrase `dev-passphrase-local`, for any clinician. **Local development only:**
seeding sets it for every clinician when, and only when, the connection it seeds over is to loopback
(judged after connecting, from the server address libpq actually used, not from the URL; `make db-reset`
asks the same of its connection before it drops anything) and the process is not in production
(`BIOSENTRY_ENV` anything but `local`, which never seeds it, whatever the host). Any other database is
seeded with no passphrases, and a clinician with none
cannot sign in: set one with `make db-passphrase id=rn-manalo` (it prompts twice, or reads one line from
stdin; at least 12 characters; it writes to the database `BIOSENTRY_DATABASE_URL` names). A database
seeded before passphrases existed has none either: `make db-reset` seeds it again.

`make db-down` stops the database (the data stays in a Docker volume). The API reaches it through
`BIOSENTRY_DATABASE_URL`, which defaults to the dev database in `compose.yaml` (its password is for
that local container only).

`VITE_API_URL` is read when the dashboard is built or started: set, it talks to the API and carries
no dataset; unset, it uses the fixture. The API allows the dashboard on `localhost` and `127.0.0.1`,
ports 5173 and 5174 (the ward sim on 5180 and 5181). The ward sim posts to `/sim/*` with the header
`X-Ingest-Key`, which must equal the API's `BIOSENTRY_INGEST_KEY` (`make api-dev` sets `dev-local`;
with the variable unset the ingest routes answer 503). There is one shared ward: `POST /sim/runs`
carries a `client_id` (one per sim page load), and while the newest run is live (not ended, last batch
or creation within 30 seconds) a different `client_id` gets 409 and nothing is written; the same one may
replace its own run. A batch for a run that is no longer the newest also gets 409.

`make check` runs lint, types and tests for both halves; the API tests need the database, so run
`make db-up` first (each test works in its own copy of a seeded database and drops it). `make db-reset`
drops every table and seeds again. `make contract` regenerates `frontend/src/types/generated.ts` from the Pydantic
schemas. Every target is in the [Makefile](Makefile).

## What you will see

- **Sign-in:** pick a department and a clinician profile, then give the passphrase. The API checks it
  (a wrong one, an unknown clinician and one with no passphrase set all get the same refusal; 5 failures
  for a clinician within 15 minutes lock that clinician out for the rest of the 15 minutes) and audits
  every attempt. The token is kept in memory only, so a reload returns to sign-in. The public demo (no
  API) checks nothing and says so.
- **Ward:** patients in priority order, each with its score and why. A patient the system
  cannot score is listed as NOT RANKED, with the reason and what to record to fix it.
- **Patient detail:** the score and what drives it, vitals and labs with where each value
  came from, notes, and trend charts.
- **Station:** admit a patient, enter lab results, or upload a document (extraction is a
  placeholder).

## What the database stores

A PostgreSQL database (the `biosentry` database of the `compose.yaml` container, or whatever
`BIOSENTRY_DATABASE_URL` names). The schema is the numbered SQL files in `backend/src/biosentry/db/migrations/`;
the API applies any not yet applied on start (`make db-migrate` does it without starting the API), and
`schema_migration` records which ran. To change the schema, add the next file
(`0002_what_it_does.sql`); never edit one that is merged. Everything is scoped to a **run**, and the newest run is the
active one: the seeded fixture ward, or a simulated ward posted to `/sim/*`.

- **Stored:** runs, clinicians and who cares for which patient, patients, every observation with its
  source, tier, time and quality (a value, or the reason there is none), 15-minute vital bins (a gap
  is NULL), notes (soft-deleted), acknowledgements, sessions (the token hashed, 12 hours), the
  audit log (ids and outcomes only, sign-in attempts included, never a passphrase; the database refuses UPDATE, DELETE, TRUNCATE and a repeated sequence number, but not a user who can drop its triggers or the table), and the
  sim's event feed.
- **Never stored:** scores, ranks, reasons and the NOT RANKED text. They are computed on every read
  (an acknowledgement keeps the score the clinician acknowledged, as they saw it).

## Status

**Built**:

- The dashboard: ward list, patient detail and station views, on a fixture provider or the API.
- The API: sign-in to a session token, server-side clinician scope, audit on every view, NEWS2 and the
  escalation arbiter computed on read, notes, labs, admission, nurse-recorded vitals,
  acknowledgements, and ingest from the ward simulation.

**Pending:** an identity provider in place of per-clinician passphrases (a clinician locked out by five
bad attempts stays locked for 15 minutes, whoever made them), cloud sync, a hash-chained audit log, Tier B in the scoring, a
signal-quality model, conformal abstention, distilled edge models, anything measured on real
patients, a sensor pipeline (IoT Device Access), document extraction, and a plain-language
explanation of each ranked alert for the nurse or physician.

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
  call. The API does this from its care assignments; the fixture provider stands in when there is no API.
- **Look:** follows the studio brand book (foundation v1), kept quiet for a clinical surface.
  Paper ground, Flour cards, Linen hairlines, Char ink; Slate only for chart lines, never text;
  Scorch marks what needs action (NEWS2 7+ solid, 5-6 or a single 3 as an edge), sage marks
  stable. Fraunces for names, Spectral for text, IBM Plex Mono for numbers and labels, Hanken
  Grotesk for buttons. No dots, shadows or gradients. Tokens are in `frontend/src/index.css`.
- **Role-aware:** each row shows the NEWS2 response for the signed-in role (RCP 2017, Chart 4):
  a nurse sees whom to call, a physician sees what assessment is due.

## Before you expose it anywhere

- **Synthetic data only.** No real patient data exists in this repository. Every run is a `fixture`
  or `synthetic` run and the database refuses a run whose `synthetic` flag is false; the flag stays on
  every run and row (I6) because a later dataset will need it.
- **Sign-in is a passphrase per clinician** (scrypt, rate-limited, audited), not an identity provider,
  and the dev passphrase exists only for a database on loopback. The public demo has no sign-in at all.
- **Keep the API off the internet.** Run it on loopback, or deploy it on the team VPS as described in
  [deploy/README.md](deploy/README.md): the API is published on the host's loopback only, the database
  not at all, and teammates reach it over Tailscale. In production (`BIOSENTRY_ENV=production`) the
  process refuses to start on the dev database password (in the URL text, and, at every connection, in the
  password libpq actually used: PGPASSWORD and a passfile count) or an ingest key that, once stripped, is
  under 32 characters or has fewer than 8 distinct ones.
  `BIOSENTRY_CORS_ORIGINS` (comma-separated) overrides the origins the dashboard may be served from; the
  local ones stay the default.

## Licence

Competition entry for the Huawei ICT Competition Innovation Track. No real patient data exists in this repository.
