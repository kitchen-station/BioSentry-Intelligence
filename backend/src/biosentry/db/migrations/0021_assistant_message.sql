-- Conversations belong to their asker and to a particular synthetic patient stay.
CREATE TABLE assistant_message (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES clinician (id),
  run_id BIGINT NOT NULL REFERENCES run (id) ON DELETE CASCADE,
  patient_id TEXT,
  scope TEXT NOT NULL CHECK (scope IN ('patient', 'ward')),
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  text TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 4000),
  lab_run_id BIGINT REFERENCES lab_run (id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL,
  FOREIGN KEY (run_id, patient_id) REFERENCES patient (run_id, id) ON DELETE CASCADE,
  CHECK ((scope = 'patient') = (patient_id IS NOT NULL)),
  UNIQUE (lab_run_id, role)
);
CREATE INDEX assistant_message_reader ON assistant_message
  (user_id, run_id, patient_id, created_at, id);
CREATE INDEX assistant_message_retention ON assistant_message (created_at);
