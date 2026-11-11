-- The ward viewer draws the live ward from a small JSON view the sim runner publishes every few
-- seconds (POST /sim/runs/{id}/view replaces it; GET /wards/{ward}/live serves it, scoped to the
-- caller's patients). It dies with the run. NULL: nothing published yet, or a fixture run.
ALTER TABLE run ADD COLUMN live_view JSONB;
