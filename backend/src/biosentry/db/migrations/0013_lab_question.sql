-- A clinician's general question about the simulated trials for the research agent (contract-ask).
-- It is a third kind of lab run: the text, and none of the compare or patient columns.
-- Both checks were named by 0011. No IF EXISTS: another name fails this migration, and the whole
-- start with it (one transaction). Rows of the two older kinds stay valid.
ALTER TABLE lab_run DROP CONSTRAINT lab_run_kind_check;
ALTER TABLE lab_run ADD CONSTRAINT lab_run_kind_check
  CHECK (kind IN ('compare', 'patient_question', 'question'));
ALTER TABLE lab_run DROP CONSTRAINT lab_run_fields_of_kind;
ALTER TABLE lab_run ADD CONSTRAINT lab_run_fields_of_kind CHECK (
  (kind = 'compare' AND question IS NULL AND run_id IS NULL AND patient_id IS NULL
    AND context IS NULL AND context_sha256 IS NULL
    AND mode IS NOT NULL AND seed IS NOT NULL AND days IS NOT NULL
    AND baseline IS NOT NULL AND change IS NOT NULL)
  OR (kind = 'patient_question' AND question IS NOT NULL AND run_id IS NOT NULL
    AND patient_id IS NOT NULL AND context IS NOT NULL AND context_sha256 IS NOT NULL
    AND mode IS NULL AND seed IS NULL AND days IS NULL AND baseline IS NULL AND change IS NULL)
  OR (kind = 'question' AND question IS NOT NULL AND run_id IS NULL AND patient_id IS NULL
    AND context IS NULL AND context_sha256 IS NULL
    AND mode IS NULL AND seed IS NULL AND days IS NULL AND baseline IS NULL AND change IS NULL)
);
