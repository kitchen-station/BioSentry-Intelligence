-- The feed the ward sim reads to learn what clinicians did (GET /sim/runs/{id}/actions, in seq
-- order). One row per successful acknowledgement, vitals entry, note or admission, written in that
-- write's own transaction. `data` holds the fields of that kind only, and never a note's text.
CREATE TABLE clinician_action (
  run_id       BIGINT NOT NULL,
  seq          BIGINT GENERATED ALWAYS AS IDENTITY,
  kind         TEXT NOT NULL CHECK (kind IN ('acknowledge', 'record_vitals', 'add_note', 'admit_patient')),
  patient_id   TEXT NOT NULL,
  clinician_id TEXT NOT NULL REFERENCES clinician (id),
  at           TIMESTAMPTZ NOT NULL,
  data         JSONB NOT NULL,
  PRIMARY KEY (run_id, seq),
  FOREIGN KEY (run_id, patient_id) REFERENCES patient (run_id, id) ON DELETE CASCADE
);
