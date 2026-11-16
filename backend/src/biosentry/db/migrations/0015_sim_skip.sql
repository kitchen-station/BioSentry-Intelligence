-- A clinician may ask the simulation to skip ahead (kind 'skip', data {"minutes": 60|360|720|1440}).
-- `run.commands_read` is the highest cursor the ward sim has fetched the command feed with: a skip
-- above it has not been picked up yet, and the API refuses a second one until it is.
ALTER TABLE sim_command
  DROP CONSTRAINT sim_command_kind_check,
  ADD CONSTRAINT sim_command_kind_check
    CHECK (kind IN ('set_speed', 'reset', 'inject_deterioration', 'start_arrhythmia', 'detach_patch', 'apply_patch', 'skip'));
ALTER TABLE run ADD COLUMN commands_read BIGINT NOT NULL DEFAULT 0 CHECK (commands_read >= 0);
