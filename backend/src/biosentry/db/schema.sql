-- BioSentry schema. Idempotent: safe to run on every start.
-- Scores, ranks and reasons are never stored: they are computed on read (NEWS2, then the arbiter).
-- Times are ISO 8601 UTC with a Z suffix, so text order is time order. Stored times are run time
-- (the fixture's clock, or the sim's); a sim run is shifted onto the wall clock when it is served.
-- The active run is always the newest run row.

CREATE TABLE IF NOT EXISTS run (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,  -- never reused, so a deleted run's orphans are never adopted
  source        TEXT NOT NULL CHECK (source IN ('fixture', 'synthetic', 'eicu_demo')),
  synthetic     INTEGER NOT NULL CHECK (synthetic = (source <> 'eicu_demo')),
  seed          INTEGER,
  ward          TEXT NOT NULL,
  origin        TEXT NOT NULL,                  -- sim minute 0
  as_of         TEXT NOT NULL,                  -- the run's "now", in run time
  last_batch_at TEXT,                           -- wall clock of the last batch; NULL for the fixture
  ended         INTEGER NOT NULL DEFAULT 0 CHECK (ended IN (0, 1)),
  tier_status   TEXT NOT NULL,                  -- JSON
  received      INTEGER NOT NULL DEFAULT 0,
  rejected      INTEGER NOT NULL DEFAULT 0,
  abstained     INTEGER NOT NULL DEFAULT 0
) STRICT;

CREATE TABLE IF NOT EXISTS clinician (
  id   TEXT PRIMARY KEY,
  role TEXT NOT NULL CHECK (role IN ('rn', 'md', 'charge_rn'))
) STRICT;

CREATE TABLE IF NOT EXISTS patient (
  run_id            INTEGER NOT NULL REFERENCES run (id) ON DELETE CASCADE,
  id                TEXT NOT NULL,
  name              TEXT NOT NULL,
  age               INTEGER,
  sex               TEXT,
  bed               TEXT NOT NULL,
  admitting_context TEXT NOT NULL,
  mechanism         TEXT NOT NULL,
  supporting        TEXT NOT NULL,              -- '' means computed from the NEWS2 breakdown on read
  trend             TEXT NOT NULL CHECK (trend IN ('rising', 'falling', 'stable', 'insufficient')),
  signal_quality    REAL,
  last_sample_at    TEXT,
  admission         TEXT,                       -- JSON, ids only; set for patients admitted in the app
  tier_b            TEXT,                       -- JSON, demo content
  tier_c            TEXT,                       -- JSON, demo content
  story             TEXT,
  PRIMARY KEY (run_id, id)
) STRICT;

CREATE TABLE IF NOT EXISTS care_assignment (
  run_id       INTEGER NOT NULL,
  patient_id   TEXT NOT NULL,
  clinician_id TEXT NOT NULL REFERENCES clinician (id),
  PRIMARY KEY (run_id, patient_id, clinician_id),
  FOREIGN KEY (run_id, patient_id) REFERENCES patient (run_id, id) ON DELETE CASCADE
) STRICT;

CREATE TABLE IF NOT EXISTS observation (
  id            INTEGER PRIMARY KEY,
  run_id        INTEGER NOT NULL,
  patient_id    TEXT NOT NULL,
  code          TEXT NOT NULL,                  -- heart_rate ... spo2_scale, or a lab key
  num           REAL,                           -- value; systolic for blood_pressure; 0/1 for oxygen
  num2          REAL,                           -- diastolic
  txt           TEXT,                           -- ACVPU
  absent_reason TEXT,
  observed_at   TEXT NOT NULL,
  source        TEXT NOT NULL CHECK (source IN ('device', 'nurse', 'lab', 'derived', 'model', 'reference')),
  tier          TEXT CHECK (tier IN ('A', 'B', 'C')),
  quality       TEXT NOT NULL CHECK (quality IN ('ok', 'degraded', 'missing')),
  model_id      TEXT,
  computed_at   TEXT,
  synthetic     INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
  -- I1: a value, or the reason there is none. Never both, never neither.
  CHECK ((num IS NOT NULL OR txt IS NOT NULL) = (absent_reason IS NULL)),
  UNIQUE (run_id, patient_id, code, observed_at, source),
  FOREIGN KEY (run_id, patient_id) REFERENCES patient (run_id, id) ON DELETE CASCADE
) STRICT;

CREATE INDEX IF NOT EXISTS observation_latest
  ON observation (run_id, patient_id, code, observed_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS vital_bin (
  run_id     INTEGER NOT NULL,
  patient_id TEXT NOT NULL,
  vital      TEXT NOT NULL,
  bin_at     TEXT NOT NULL,                     -- start of the 15-minute bin
  value      REAL,                              -- NULL is a gap: never zero, never filled
  PRIMARY KEY (run_id, patient_id, vital, bin_at),
  FOREIGN KEY (run_id, patient_id) REFERENCES patient (run_id, id) ON DELETE CASCADE
) STRICT;

CREATE TABLE IF NOT EXISTS clinical_note (
  run_id     INTEGER NOT NULL,
  id         TEXT NOT NULL,
  patient_id TEXT NOT NULL,
  author     TEXT NOT NULL,
  role       TEXT NOT NULL,
  timestamp  TEXT NOT NULL,
  content    TEXT NOT NULL,
  synthetic  INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
  deleted_at TEXT,
  PRIMARY KEY (run_id, id),
  FOREIGN KEY (run_id, patient_id) REFERENCES patient (run_id, id) ON DELETE CASCADE
) STRICT;

CREATE TABLE IF NOT EXISTS acknowledgement (
  run_id       INTEGER NOT NULL,
  patient_id   TEXT NOT NULL,
  clinician_id TEXT NOT NULL REFERENCES clinician (id),
  score        INTEGER NOT NULL,
  at           TEXT NOT NULL,
  PRIMARY KEY (run_id, patient_id, clinician_id),
  FOREIGN KEY (run_id, patient_id) REFERENCES patient (run_id, id) ON DELETE CASCADE
) STRICT;

CREATE TABLE IF NOT EXISTS session (
  token_hash   TEXT PRIMARY KEY,                -- sha256 of the bearer token; the token itself is never stored
  clinician_id TEXT NOT NULL REFERENCES clinician (id),
  created_at   TEXT NOT NULL,
  expires_at   TEXT NOT NULL
) STRICT;

-- Ids and outcomes only (I7). No FK to run: purging a run leaves the trail.
-- The triggers make the database refuse UPDATE, DELETE and an INSERT that reuses a seq (which
-- INSERT OR REPLACE would otherwise use to overwrite a row). They do not stop anyone who can
-- drop a trigger or the table.
CREATE TABLE IF NOT EXISTS audit_log (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  at           TEXT NOT NULL,
  clinician_id TEXT,
  role         TEXT,
  action       TEXT NOT NULL,
  resource     TEXT NOT NULL,
  outcome      TEXT NOT NULL
) STRICT;

CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_overwrite BEFORE INSERT ON audit_log
WHEN EXISTS (SELECT 1 FROM audit_log WHERE seq = NEW.seq)
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

-- Demo only: the sim's event feed.
CREATE TABLE IF NOT EXISTS sim_event (
  run_id     INTEGER NOT NULL REFERENCES run (id) ON DELETE CASCADE,
  seq        INTEGER NOT NULL,
  minute     INTEGER NOT NULL,
  kind       TEXT NOT NULL,
  patient_id TEXT,
  staff_id   TEXT,
  text       TEXT NOT NULL,
  PRIMARY KEY (run_id, seq)
) STRICT;
