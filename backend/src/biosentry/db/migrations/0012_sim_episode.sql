-- The simulator's episode truth for a live run (contract-truth v1.1): when a simulated deterioration
-- began. One row for each patient of a run, replaced when the simulator sends other values. Only the
-- history route reads it, and only for a run marked synthetic. It goes with the run and the patient.
CREATE TABLE sim_episode (
  run_id       BIGINT      NOT NULL,
  patient_id   TEXT        NOT NULL,
  kind         TEXT        NOT NULL CONSTRAINT sim_episode_kind CHECK (kind IN ('respiratory', 'tachycardic', 'hypotensive')),
  start_minute INTEGER     NOT NULL CONSTRAINT sim_episode_start CHECK (start_minute >= 0),
  full_minute  INTEGER     NOT NULL,
  received_at  TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (run_id, patient_id),
  CONSTRAINT sim_episode_full CHECK (full_minute >= start_minute),
  FOREIGN KEY (run_id, patient_id) REFERENCES patient (run_id, id) ON DELETE CASCADE
);
