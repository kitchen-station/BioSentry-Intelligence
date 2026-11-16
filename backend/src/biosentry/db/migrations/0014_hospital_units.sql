-- A run is one hospital of 1 to 12 units (contract: hospital-units). A patient row is one stay in
-- one unit. Every existing run becomes a run of one ranked unit, its ward; its live view moves
-- with it. run.ward stays as the home unit; run.live_view is no longer read or written.
CREATE TABLE run_unit (
  run_id    BIGINT NOT NULL REFERENCES run (id) ON DELETE CASCADE,
  id        TEXT NOT NULL CHECK (id ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,19}$'),
  position  INT NOT NULL CHECK (position BETWEEN 0 AND 11),
  name      TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
  floor     INT CHECK (floor BETWEEN -5 AND 200),
  kind      TEXT NOT NULL CHECK (kind IN ('ranked', 'shown', 'unranked')),
  live_view JSONB,
  PRIMARY KEY (run_id, id),
  UNIQUE (run_id, position)
);
INSERT INTO run_unit (run_id, id, position, name, floor, kind, live_view)
  SELECT id, ward, 0, ward, NULL, 'ranked', live_view FROM run;

ALTER TABLE patient
  ADD COLUMN unit    TEXT,
  ADD COLUMN person  TEXT CHECK (length(person) BETWEEN 1 AND 64),
  ADD COLUMN to_unit TEXT;
UPDATE patient p SET unit = r.ward FROM run r WHERE r.id = p.run_id;
ALTER TABLE patient
  ALTER COLUMN unit SET NOT NULL,
  ADD FOREIGN KEY (run_id, unit) REFERENCES run_unit (run_id, id),
  ADD FOREIGN KEY (run_id, to_unit) REFERENCES run_unit (run_id, id),
  DROP CONSTRAINT patient_outcome_check,
  ADD CONSTRAINT patient_outcome_check
    CHECK (outcome IN ('discharged', 'higher_care', 'transferred', 'died')),
  ADD CONSTRAINT patient_transfer_has_unit
    CHECK ((to_unit IS NOT NULL) = (outcome IS NOT DISTINCT FROM 'transferred')),
  ADD CONSTRAINT patient_not_to_own_unit CHECK (to_unit IS DISTINCT FROM unit);

-- A clinician's own unit is the default unit of a patient they admit. It never widens what they see.
ALTER TABLE clinician ADD COLUMN unit TEXT;
UPDATE clinician SET unit = 'W4' WHERE role <> 'admin';
-- One charge nurse for each other unit, with no passphrase: nobody can sign in until the owner sets one.
INSERT INTO clinician (id, role, unit) VALUES
  ('cn-er', 'charge_rn', 'ER'), ('cn-icu', 'charge_rn', 'ICU'), ('cn-med3a', 'charge_rn', 'MED3A'),
  ('cn-med3b', 'charge_rn', 'MED3B'), ('cn-surg5a', 'charge_rn', 'SURG5A')
  ON CONFLICT (id) DO NOTHING;

ALTER TABLE sim_command ADD COLUMN unit TEXT;
ALTER TABLE clinician_action ADD COLUMN unit TEXT;
