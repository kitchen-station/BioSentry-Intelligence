-- Capacity is fixed for a run's unit. The other figures are the latest accepted batch's
-- snapshot; NULL means that the simulator has not supplied one. Existing runs keep NULL.
ALTER TABLE run_unit
  ADD COLUMN capacity INT CHECK (capacity BETWEEN 1 AND 1000),
  ADD COLUMN nurses_on_duty INT CHECK (nurses_on_duty >= 0),
  ADD COLUMN awaiting_icu INT CHECK (awaiting_icu >= 0),
  ADD COLUMN outliers INT CHECK (outliers >= 0);
