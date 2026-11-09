-- A patient row is one stay. NULL admitted_at: the arrival is not known (the seeded ward and the
-- patients already on a ward when a sim run begins). NULL left_at: still on the ward.
ALTER TABLE patient
  ADD COLUMN admitted_at TIMESTAMPTZ,
  ADD COLUMN left_at     TIMESTAMPTZ,
  ADD COLUMN outcome     TEXT CHECK (outcome IN ('discharged', 'higher_care')),
  ADD CONSTRAINT patient_left_has_outcome CHECK ((left_at IS NULL) = (outcome IS NULL));
