-- A clinician's question about ONE patient for the research agent (contract-patient-ask). The context
-- is the allow-listed data the asker could see at ask time, frozen as canonical JSON text with its
-- sha256: the record of what the agent saw. It goes with the run (ON DELETE CASCADE).
-- 0010 declared the kind CHECK inline, so PostgreSQL named it lab_run_kind_check. No IF EXISTS:
-- another name fails this migration, and the whole start with it (one transaction).
ALTER TABLE lab_run DROP CONSTRAINT lab_run_kind_check;
ALTER TABLE lab_run ADD CONSTRAINT lab_run_kind_check CHECK (kind IN ('compare', 'patient_question'));
ALTER TABLE lab_run
  ALTER COLUMN mode DROP NOT NULL,
  ALTER COLUMN seed DROP NOT NULL,
  ALTER COLUMN days DROP NOT NULL,
  ALTER COLUMN baseline DROP NOT NULL,
  ALTER COLUMN change DROP NOT NULL,
  ADD COLUMN question TEXT CHECK (length(question) BETWEEN 1 AND 500 AND strpos(question, E'\n') = 0),
  ADD COLUMN run_id BIGINT,
  ADD COLUMN patient_id TEXT,
  ADD COLUMN context TEXT CHECK (octet_length(context) <= 262144),
  ADD COLUMN context_sha256 TEXT CHECK (context_sha256 ~ '^[0-9a-f]{64}$'),
  ADD CONSTRAINT lab_run_patient FOREIGN KEY (run_id, patient_id)
    REFERENCES patient (run_id, id) ON DELETE CASCADE;
ALTER TABLE lab_run ADD CONSTRAINT lab_run_fields_of_kind CHECK (
  (kind = 'compare' AND question IS NULL AND run_id IS NULL AND patient_id IS NULL
    AND context IS NULL AND context_sha256 IS NULL
    AND mode IS NOT NULL AND seed IS NOT NULL AND days IS NOT NULL
    AND baseline IS NOT NULL AND change IS NOT NULL)
  OR (kind = 'patient_question' AND question IS NOT NULL AND run_id IS NOT NULL
    AND patient_id IS NOT NULL AND context IS NOT NULL AND context_sha256 IS NOT NULL
    AND mode IS NULL AND seed IS NULL AND days IS NULL AND baseline IS NULL AND change IS NULL)
);
CREATE INDEX lab_run_by_patient ON lab_run (run_id, patient_id, id) WHERE patient_id IS NOT NULL;
