-- BioSentry schema (PostgreSQL), migration 0001: the schema as it stood before migrations.
-- Keep it idempotent (IF NOT EXISTS, OR REPLACE): it also runs on databases that schema.sql built,
-- and there it must change nothing. Later migrations need not be idempotent; each runs once.
-- Scores, ranks and reasons are computed on read (NEWS2, then the arbiter), not stored. The one
-- exception is acknowledgement.score: the score the clinician acknowledged, kept as they saw it.
-- Times are TIMESTAMPTZ, written in whole seconds and served as ISO 8601 UTC with a Z suffix. Stored
-- times are run time (the fixture's clock, or the sim's); a sim run is shifted onto the wall clock when
-- it is served. The active run is always the newest run row.
-- Floats are DOUBLE PRECISION: a 4-byte REAL would lose precision on clinical values.

CREATE TABLE IF NOT EXISTS run (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,  -- never reused, so a deleted run's orphans are never adopted
  source        TEXT NOT NULL CHECK (source IN ('fixture', 'synthetic', 'eicu_demo')),
  synthetic     BOOLEAN NOT NULL CHECK (synthetic = (source <> 'eicu_demo')),
  seed          BIGINT,
  ward          TEXT NOT NULL,
  origin        TIMESTAMPTZ NOT NULL,           -- sim minute 0
  as_of         TIMESTAMPTZ NOT NULL,           -- the run's "now", in run time
  last_batch_at TIMESTAMPTZ,                    -- wall clock of the last batch; NULL for the fixture
  ended         BOOLEAN NOT NULL DEFAULT FALSE,
  tier_status   JSONB NOT NULL,
  received      BIGINT NOT NULL DEFAULT 0,
  rejected      BIGINT NOT NULL DEFAULT 0,
  abstained     BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS clinician (
  id   TEXT PRIMARY KEY,
  role TEXT NOT NULL CHECK (role IN ('rn', 'md', 'charge_rn'))
);

CREATE TABLE IF NOT EXISTS patient (
  run_id            BIGINT NOT NULL REFERENCES run (id) ON DELETE CASCADE,
  id                TEXT NOT NULL,
  name              TEXT NOT NULL,
  age               INTEGER,
  sex               TEXT,
  bed               TEXT NOT NULL,
  admitting_context TEXT NOT NULL,
  mechanism         TEXT NOT NULL,
  supporting        TEXT NOT NULL,              -- '' means computed from the NEWS2 breakdown on read
  trend             TEXT NOT NULL CHECK (trend IN ('rising', 'falling', 'stable', 'insufficient')),
  signal_quality    DOUBLE PRECISION,
  last_sample_at    TIMESTAMPTZ,
  admission         JSONB,                      -- ids only; set for patients admitted in the app
  tier_b            JSONB,                      -- demo content
  tier_c            JSONB,                      -- demo content
  story             TEXT,
  PRIMARY KEY (run_id, id)
);

CREATE TABLE IF NOT EXISTS care_assignment (
  run_id       BIGINT NOT NULL,
  patient_id   TEXT NOT NULL,
  clinician_id TEXT NOT NULL REFERENCES clinician (id),
  PRIMARY KEY (run_id, patient_id, clinician_id),
  FOREIGN KEY (run_id, patient_id) REFERENCES patient (run_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS observation (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id        BIGINT NOT NULL,
  patient_id    TEXT NOT NULL,
  code          TEXT NOT NULL,                  -- heart_rate ... spo2_scale, or a lab key
  num           DOUBLE PRECISION,               -- value; systolic for blood_pressure; 0/1 for oxygen
  num2          DOUBLE PRECISION,               -- diastolic
  txt           TEXT,                           -- ACVPU
  absent_reason TEXT,
  observed_at   TIMESTAMPTZ NOT NULL,
  source        TEXT NOT NULL CHECK (source IN ('device', 'nurse', 'lab', 'derived', 'model', 'reference')),
  tier          TEXT CHECK (tier IN ('A', 'B', 'C')),
  quality       TEXT NOT NULL CHECK (quality IN ('ok', 'degraded', 'missing')),
  model_id      TEXT,
  computed_at   TIMESTAMPTZ,
  synthetic     BOOLEAN NOT NULL,
  -- I1: a value, or the reason there is none. Never both, never neither.
  CHECK ((num IS NOT NULL OR txt IS NOT NULL) = (absent_reason IS NULL)),
  UNIQUE (run_id, patient_id, code, observed_at, source),
  FOREIGN KEY (run_id, patient_id) REFERENCES patient (run_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS observation_latest
  ON observation (run_id, patient_id, code, observed_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS vital_bin (
  run_id     BIGINT NOT NULL,
  patient_id TEXT NOT NULL,
  vital      TEXT NOT NULL,
  bin_at     TIMESTAMPTZ NOT NULL,              -- start of the 15-minute bin
  value      DOUBLE PRECISION,                  -- NULL is a gap: never zero, never filled
  PRIMARY KEY (run_id, patient_id, vital, bin_at),
  FOREIGN KEY (run_id, patient_id) REFERENCES patient (run_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS clinical_note (
  run_id     BIGINT NOT NULL,
  id         TEXT NOT NULL,
  seq        BIGINT GENERATED ALWAYS AS IDENTITY,  -- insertion order: notes are read newest first
  patient_id TEXT NOT NULL,
  author     TEXT NOT NULL,
  role       TEXT NOT NULL,
  timestamp  TIMESTAMPTZ NOT NULL,
  content    TEXT NOT NULL,
  synthetic  BOOLEAN NOT NULL,
  deleted_at TIMESTAMPTZ,
  PRIMARY KEY (run_id, id),
  FOREIGN KEY (run_id, patient_id) REFERENCES patient (run_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS acknowledgement (
  run_id       BIGINT NOT NULL,
  patient_id   TEXT NOT NULL,
  clinician_id TEXT NOT NULL REFERENCES clinician (id),
  score        INTEGER NOT NULL,
  at           TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (run_id, patient_id, clinician_id),
  FOREIGN KEY (run_id, patient_id) REFERENCES patient (run_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS session (
  token_hash   TEXT PRIMARY KEY,                -- sha256 of the bearer token; the token itself is never stored
  clinician_id TEXT NOT NULL REFERENCES clinician (id),
  created_at   TIMESTAMPTZ NOT NULL,
  expires_at   TIMESTAMPTZ NOT NULL
);

-- Ids and outcomes only (I7). No FK to run: purging a run leaves the trail.
-- The triggers make the database refuse UPDATE, DELETE and TRUNCATE; the primary key refuses a
-- repeated seq. They do not stop anyone who can drop a trigger or the table.
CREATE TABLE IF NOT EXISTS audit_log (
  seq          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  at           TIMESTAMPTZ NOT NULL,
  clinician_id TEXT,
  role         TEXT,
  action       TEXT NOT NULL,
  resource     TEXT NOT NULL,
  outcome      TEXT NOT NULL
);

CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_log is append-only' USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

CREATE OR REPLACE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();

CREATE OR REPLACE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();

CREATE OR REPLACE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only();

-- Demo only: the sim's event feed.
CREATE TABLE IF NOT EXISTS sim_event (
  run_id     BIGINT NOT NULL REFERENCES run (id) ON DELETE CASCADE,
  seq        BIGINT NOT NULL,
  minute     INTEGER NOT NULL,
  kind       TEXT NOT NULL,
  patient_id TEXT,
  staff_id   TEXT,
  text       TEXT NOT NULL,
  PRIMARY KEY (run_id, seq)
);
