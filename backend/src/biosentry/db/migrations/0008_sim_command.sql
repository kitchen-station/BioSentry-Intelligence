-- The feed the ward sim reads to learn what a clinician asked of the simulation (GET
-- /sim/runs/{id}/commands, in seq order). One row per accepted POST /wards/{ward}/sim/commands,
-- written in its own transaction. `data` holds the fields of that kind only ({"speed": n} for
-- set_speed, else {}). The run's own cascade, as sim_event has, removes the rows of a command that
-- names no patient: the patient key alone would leave those behind.
CREATE TABLE sim_command (
  run_id       BIGINT NOT NULL REFERENCES run (id) ON DELETE CASCADE,
  seq          BIGINT GENERATED ALWAYS AS IDENTITY,
  kind         TEXT NOT NULL CHECK (kind IN ('set_speed', 'reset', 'inject_deterioration', 'start_arrhythmia', 'detach_patch', 'apply_patch')),
  patient_id   TEXT,
  clinician_id TEXT NOT NULL REFERENCES clinician (id),
  at           TIMESTAMPTZ NOT NULL,
  data         JSONB NOT NULL,
  PRIMARY KEY (run_id, seq),
  FOREIGN KEY (run_id, patient_id) REFERENCES patient (run_id, id) ON DELETE CASCADE
);
