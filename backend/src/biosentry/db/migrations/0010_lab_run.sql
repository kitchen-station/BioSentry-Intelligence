-- A clinician's request for one simulated compare run (contract-d7). The API only stores it; the lab
-- worker claims it with the ingest key, runs the simulator's compare and posts its two files back.
-- No key to run or patient: a request cannot name the live run or a patient. The values come from
-- fixed lists (schemas/lab.py); the patterns here are a second fence: no path characters.
CREATE TABLE lab_run (
  id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ward         TEXT NOT NULL CHECK (length(ward) BETWEEN 1 AND 64),
  requested_by TEXT NOT NULL REFERENCES clinician (id),
  requested_at TIMESTAMPTZ NOT NULL,
  kind         TEXT NOT NULL CHECK (kind IN ('compare')),
  mode         TEXT NOT NULL CHECK (mode IN ('w4', 'hospital')),
  seed         BIGINT NOT NULL CHECK (seed BETWEEN 0 AND 2147483647),
  days         INT NOT NULL CHECK (days BETWEEN 1 AND 30),
  baseline     TEXT NOT NULL CHECK (baseline ~ '^[a-z0-9_-]{1,40}$'),
  change       TEXT NOT NULL CHECK (change ~ '^[a-z0-9_=-]{1,40}$'),
  state        TEXT NOT NULL CHECK (state IN ('queued', 'running', 'done', 'failed', 'refused')),
  claimed_at   TIMESTAMPTZ,
  finished_at  TIMESTAMPTZ,
  report       JSONB,
  report_text  TEXT,
  reason       TEXT CHECK (length(reason) BETWEEN 1 AND 300),
  CHECK ((state = 'done') = (report IS NOT NULL AND report_text IS NOT NULL)),
  CHECK ((report IS NULL) = (report_text IS NULL)),
  CHECK ((state IN ('failed', 'refused')) = (reason IS NOT NULL)),
  CHECK (state <> 'queued' OR claimed_at IS NULL),
  CHECK (state NOT IN ('running', 'done', 'refused') OR claimed_at IS NOT NULL),
  CHECK ((state IN ('done', 'failed', 'refused')) = (finished_at IS NOT NULL))
);
CREATE INDEX lab_run_open ON lab_run (state, id);
CREATE INDEX lab_run_by ON lab_run (requested_by, requested_at);
