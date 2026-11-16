-- The newest stored row of each kind (nurse or not, a value or an absence) of each code of one patient is
-- read on every ward view. The old index could not say the kind, so the read walked all of a patient's
-- rows (thousands over a week) to keep eight. With the kind in the key each answer is one index probe.
CREATE INDEX IF NOT EXISTS observation_latest_kind
  ON observation (run_id, patient_id, code, (source = 'nurse'), (absent_reason IS NULL), observed_at DESC, id DESC);
