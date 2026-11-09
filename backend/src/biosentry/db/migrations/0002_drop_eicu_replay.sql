-- The eICU replay is dropped (another dataset comes later). Its runs go (patients, observations,
-- bins, notes, acknowledgements and the sim feed cascade); audit_log has no foreign key to run
-- and stays. A run is a fixture or synthetic, and until a dataset of real records returns,
-- `synthetic` must be true. The flag stays on every run and row (I6).

DELETE FROM run WHERE source = 'eicu_demo';

-- The two CHECKs on run were never named; look them up (both mention eicu_demo) rather than guess.
DO $$
DECLARE c TEXT;
BEGIN
  FOR c IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'run'::regclass AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%eicu_demo%'
  LOOP
    EXECUTE format('ALTER TABLE run DROP CONSTRAINT %I', c);
  END LOOP;
END $$;

ALTER TABLE run ADD CONSTRAINT run_source_check CHECK (source IN ('fixture', 'synthetic'));
ALTER TABLE run ADD CONSTRAINT run_synthetic_check CHECK (synthetic);
